"""Optional loopback-only Qwen inference service; separate from the desktop runtime."""
import argparse
import base64
import io
import json
import math
import struct
import sys
import hashlib
from collections import OrderedDict
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from time import monotonic

from core.scene_protocol import expand_scene, partial_description


def load_runtime_config(path):
    path = Path(path).resolve()
    receipt = json.loads(path.read_text(encoding='utf-8'))
    model_path = Path(receipt['path'])
    receipt['path'] = str((path.parent / model_path).resolve() if not model_path.is_absolute() else model_path)
    if receipt.get('torch_site'):
        torch_path = Path(receipt['torch_site'])
        receipt['torch_site'] = str((path.parent / torch_path).resolve() if not torch_path.is_absolute() else torch_path)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=11435)
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    receipt = load_runtime_config(args.config or root / '.cache/qwen-local-runtime.json')
    language_precision = receipt.get('language_precision', 'nf4')
    if language_precision not in ('nf4', 'fp16'):
        raise ValueError('Invalid local language precision')
    torch_site = receipt.get('torch_site')
    # Prefer the already verified CUDA build without changing its installation.
    import numpy
    if torch_site:
        sys.path.insert(0, torch_site)
    import torch
    if torch_site:
        sys.path.remove(torch_site)
        if torch_site not in sys.path:
            sys.path.append(torch_site)
    from PIL import Image
    from transformers import AutoConfig, AutoModelForImageTextToText, AutoProcessor, TextIteratorStreamer
    from accelerate import init_empty_weights
    from accelerate.utils import set_module_tensor_to_device
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    torch.set_num_threads(2)
    print('Loading local Qwen model on ' + device, flush=True)
    processor = AutoProcessor.from_pretrained(receipt['path'], local_files_only=True,
        min_pixels=64 * 32 * 32, max_pixels=384 * 32 * 32)
    print('Vision processor ready; loading checkpoint', flush=True)
    config = AutoConfig.from_pretrained(receipt['path'], local_files_only=True)
    # Transformers' flat torch-storage mmap slicing crashes on this Windows
    # CUDA build with a >4 GiB checkpoint. Safetensors loads each named tensor
    # directly, keeping the same verified weights and avoiding that fast path.
    with init_empty_weights():
        model = AutoModelForImageTextToText.from_config(config, torch_dtype=torch.float16, attn_implementation='sdpa')
    quantized = False
    if device == 'cuda' and language_precision == 'nf4':
        import bitsandbytes as bnb
        # Quantize language projection layers; retain vision and embeddings in
        # FP16 to reduce the 16-GiB host's concurrent video/AI memory pressure.
        for module_name, module in list(model.named_modules()):
            if module_name.startswith('model.language_model.layers.') and isinstance(module, torch.nn.Linear):
                parent_name, attribute = module_name.rsplit('.', 1)
                parent = model.get_submodule(parent_name)
                replacement = bnb.nn.Linear4bit(module.in_features, module.out_features,
                    bias=module.bias is not None, compute_dtype=torch.float16,
                    compress_statistics=True, quant_type='nf4', device='meta')
                setattr(parent, attribute, replacement)
                quantized = True
    checkpoint = Path(receipt['path']) / 'model.safetensors'
    # Read individual tensor ranges instead of asking torch to slice a single
    # >4 GiB mapped storage on Windows. This keeps peak CPU allocation bounded.
    dtypes = {'BF16': torch.bfloat16, 'F16': torch.float16, 'F32': torch.float32,
              'I64': torch.int64, 'I32': torch.int32, 'BOOL': torch.bool}
    with checkpoint.open('rb') as stream:
        header_size = struct.unpack('<Q', stream.read(8))[0]
        if header_size > 16 * 1024 * 1024:
            raise RuntimeError('Invalid checkpoint header')
        header = json.loads(stream.read(header_size))
        entries = {key: value for key, value in header.items() if key != '__metadata__'}
        missing = set(model.state_dict()) - set(entries) - {'lm_head.weight'}
        extra = set(entries) - set(model.state_dict())
        if missing or extra:
            raise RuntimeError('Qwen checkpoint architecture mismatch')
        total = len(entries)
        for index, (name, info) in enumerate(entries.items()):
            dtype = dtypes[info['dtype']]
            start, end = info['data_offsets']
            expected_bytes = math.prod(info['shape']) * torch.empty((), dtype=dtype).element_size()
            if end - start != expected_bytes or not 0 <= start < end <= checkpoint.stat().st_size - 8 - header_size:
                raise RuntimeError('Invalid checkpoint tensor range')
            stream.seek(8 + header_size + start)
            raw = bytearray(expected_bytes)
            if stream.readinto(raw) != expected_bytes:
                raise RuntimeError('Incomplete checkpoint tensor')
            tensor = torch.frombuffer(raw, dtype=dtype).reshape(info['shape'])
            parent_name, attribute = name.rsplit('.', 1)
            parent = model.get_submodule(parent_name)
            if quantized and attribute == 'weight' and isinstance(parent, bnb.nn.Linear4bit):
                parent._parameters['weight'] = bnb.nn.Params4bit(tensor.to(torch.float16), requires_grad=False,
                    compress_statistics=True, quant_type='nf4', module=parent).to(device)
            else:
                set_module_tensor_to_device(model, name, device, value=tensor,
                    dtype=torch.float16 if dtype.is_floating_point and device == 'cuda' else None)
            del tensor, raw
            if (index + 1) % 150 == 0:
                print(f'Loaded {index+1}/{total} tensors', flush=True)
    model.tie_weights()
    for module in model.modules():
        for name, buffer in module._buffers.items():
            if buffer is not None and not buffer.is_meta:
                module._buffers[name] = buffer.to(device)
    model.eval()
    print('Qwen checkpoint loaded; language NF4=' + str(quantized), flush=True)
    inference_lock = Lock()
    cache_lock = Lock()
    cache = OrderedDict()
    state = {'busy': False, 'last_timing': None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def reply(self, status, value):
            data = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def do_GET(self):
            if self.path == '/api/version':
                self.reply(200, {'version': 'fireguard-qwen-local-v5', 'ready': True, 'language_nf4': quantized,
                                 'language_precision': 'nf4' if quantized else 'fp16',
                                 'streaming_descriptions': True, 'compact_review': True})
            elif self.path == '/api/status':
                self.reply(200, {**state, 'device': device})
            elif self.path == '/api/tags':
                self.reply(200, {'models': [{'name': 'qwen3-vl:2b', 'model': receipt['model'], 'digest': receipt['revision']}]})
            else:
                self.reply(404, {'error': 'not_found'})

        def do_POST(self):
            if self.path != '/api/chat':
                self.reply(404, {'error': 'not_found'})
                return
            started = monotonic()
            acquired, streaming = False, False
            generation_thread = None

            def begin_stream():
                nonlocal streaming
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.end_headers()
                streaming = True

            def emit(value):
                try:
                    self.wfile.write((json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8'))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 8 * 1024 * 1024:
                    raise ValueError('invalid_request_size')
                request_bytes = self.rfile.read(length)
                request = json.loads(request_bytes)
                if request.get('model') not in ('qwen3-vl:2b', receipt['model']):
                    raise ValueError('model_not_available')
                message = request['messages'][-1]
                images = message.get('images', [])
                if not 1 <= len(images) <= 3 or not isinstance(message.get('content'), str) or len(message['content']) > 12000:
                    raise ValueError('invalid_images_or_prompt')
                options = request.get('options', {})
                fast = options.get('review_profile') == 'fast'
                budget = (160 if fast else 384) * 32 * 32
                max_tokens = min(320 if fast else 800, max(32, int(options.get('num_predict', 288 if fast else 640))))
                cache_key = hashlib.sha256(json.dumps({'model': receipt['revision'], 'message': message,
                    'format': request.get('format'), 'profile': 'fast' if fast else 'detailed',
                    'budget': budget, 'max_tokens': max_tokens}, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
                with cache_lock:
                    cached = deepcopy(cache.get(cache_key)) if not options.get('no_cache') else None
                    if cached:
                        cache.move_to_end(cache_key)
                if cached:
                    cached['runtime'].update(cache_hit=True, source_generation_s=cached['runtime']['generation_s'],
                                             generation_s=0., request_s=round(monotonic() - started, 3))
                    if request.get('stream'):
                        begin_stream()
                        scene = json.loads(cached['message']['content'])
                        emit({'event': 'description', 'description': scene['description'], 'preliminary': True})
                        emit({'event': 'result', 'result': cached})
                    else:
                        self.reply(200, cached)
                    return
                if not inference_lock.acquire(blocking=False):
                    self.reply(429, {'error': 'local_model_busy'})
                    return
                acquired = True
                state['busy'] = True
                if request.get('stream'):
                    begin_stream()
                    emit({'event': 'stage', 'stage': 'preparing'})
                content = []
                for encoded in images:
                    data = base64.b64decode(encoded, validate=True)
                    if len(data) > 2 * 1024 * 1024:
                        raise ValueError('image_too_large')
                    image = Image.open(io.BytesIO(data))
                    if image.width * image.height > 4_000_000:
                        raise ValueError('image_too_large')
                    content.append({'type': 'image', 'image': image.convert('RGB')})
                prompt_text = message['content']
                prompt_text += '\n返回这个对象的实际字段值：\n' + json.dumps(request.get('format'), ensure_ascii=False)
                content.append({'type': 'text', 'text': prompt_text})
                inputs = processor.apply_chat_template([{'role': 'user', 'content': content}],
                    tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors='pt',
                    images_kwargs={'min_pixels': 64 * 32 * 32, 'max_pixels': budget}).to(device)
                prepared = monotonic()
                streamer = TextIteratorStreamer(processor.tokenizer, skip_prompt=True, skip_special_tokens=True,
                                                clean_up_tokenization_spaces=False)
                generated_result = {}
                def generate():
                    try:
                        with torch.inference_mode():
                            generated_result['tokens'] = model.generate(**inputs, max_new_tokens=max_tokens,
                                do_sample=False, streamer=streamer, pad_token_id=processor.tokenizer.eos_token_id)
                    except Exception as exc:
                        generated_result['error'] = exc
                        streamer.on_finalized_text('', stream_end=True)
                generation_thread = Thread(target=generate, name='FireGuard-Qwen-generation', daemon=True)
                generation_thread.start()
                text, first_description, last_preview = '', None, ''
                for delta in streamer:
                    text += delta
                    preview = partial_description(text)
                    if len(preview) >= 4 and preview != last_preview:
                        if first_description is None:
                            first_description = round(monotonic() - started, 3)
                        if len(preview) - len(last_preview) >= 4:
                            if streaming:
                                emit({'event': 'description', 'description': preview, 'preliminary': True})
                            last_preview = preview
                generation_thread.join()
                if 'error' in generated_result:
                    raise generated_result['error']
                generated = generated_result['tokens']
                # Reject incomplete or non-JSON output. Desktop also validates semantic fields.
                start, end = text.find('{'), text.rfind('}')
                parsed = expand_scene(json.loads(text[start:end + 1]))
                # Never cache or present an incomplete model output as reviewed.
                from core.scene_review import validate_scene
                parsed = validate_scene(parsed, len(images), 1. if len(images) > 1 else 0.)
                timing = {'prepare_s': round(prepared - started, 3), 'generation_s': round(monotonic() - prepared, 3),
                          'first_description_s': first_description,
                          'input_tokens': int(inputs['input_ids'].shape[1]),
                          'output_tokens': int(generated.shape[1] - inputs['input_ids'].shape[1]),
                          'max_image_pixels': budget, 'profile': 'fast' if fast else 'detailed', 'cache_hit': False}
                state['last_timing'] = timing
                result = {'model': receipt['model'] + '@' + receipt['revision'][:12],
                          'message': {'role': 'assistant', 'content': json.dumps(parsed, ensure_ascii=False)},
                          'done': True, 'runtime': {'language_nf4': quantized,
                            'language_precision': 'nf4' if quantized else 'fp16',
                            'vision_dtype': 'float16' if device == 'cuda' else 'float32', **timing},
                          'total_duration': int((monotonic() - started) * 1e9)}
                with cache_lock:
                    cache[cache_key] = deepcopy(result)
                    while len(cache) > 16:
                        cache.popitem(last=False)
                if streaming:
                    emit({'event': 'result', 'result': result})
                else:
                    self.reply(200, result)
                print(f'Qwen review completed in {monotonic() - started:.2f}s; {len(images)} images', flush=True)
            except Exception as exc:
                print('Qwen request failed: ' + type(exc).__name__ + ': ' + str(exc)[:300], flush=True)
                if 'text' in locals():
                    print('Unvalidated scene output: ' + text[:1600], flush=True)
                if streaming:
                    emit({'event': 'error', 'error': 'scene_review_failed'})
                else:
                    self.reply(422, {'error': 'scene_review_failed'})
            finally:
                if generation_thread and generation_thread.is_alive():
                    generation_thread.join()
                if acquired:
                    state['busy'] = False
                    inference_lock.release()

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    print(f'Local Qwen ready on 127.0.0.1:{args.port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
