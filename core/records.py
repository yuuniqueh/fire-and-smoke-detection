"""Read completed local runs and export portable evidence without external services."""
import csv
import json
import os
import re
from pathlib import Path
import tempfile
import zipfile
from collections import deque
from datetime import datetime
from core.alert_presentation import LEVEL_NAMES
from core.review_workflow import phase, reopen_action

REVIEW_COMPLETE = frozenset(('false_positive', 'normal_use', 'resolved'))


def read_json(path):
    return Path(path).read_text(encoding='utf-8')


def read_jsonl_tail(path, limit=500):
    """Bounded display history; malformed incomplete final lines are ignored."""
    rows = deque(maxlen=limit)
    path = Path(path)
    if path.is_file():
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    return list(rows)


def append_feedback(run_path, notification, action, comment=''):
    """Store an explicit human action, without changing predictions or labels."""
    root = Path(run_path).resolve()
    if action not in ('acknowledged', 'false_positive', 'needs_verification', 'uncertain', 'normal_use', 'confirmed_abnormal', 'resolved', 'reopened'):
        raise ValueError('未知提示反馈类型。')
    if not isinstance(comment, str) or len(comment) > 1000:
        raise ValueError('反馈说明最多 1000 字符。')
    notification_id = notification.get('notification_id')
    if notification_id is None:
        raise ValueError('提示编号缺失。')
    path = root / 'alerts.jsonl'
    if not path.is_file() or path.is_symlink() or (root / 'feedback.jsonl').is_symlink():
        raise ValueError('提示记录不存在或路径无效。')
    stored = None
    latest_actionable = None
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                if item.get('event_id') == notification.get('event_id') and item.get('kind') != 'event_closed':
                    latest_actionable = item
                if item.get('notification_id') == notification_id:
                    stored = item
                    if action != 'reopened':
                        break
    if stored is None or stored.get('event_id') != notification.get('event_id'):
        raise ValueError('无法在本次分析中核对该提示。')
    record = {'notification_id': notification_id, 'event_id': stored['event_id'],
              'action': action, 'comment': comment.strip(),
              'created_at': datetime.now().astimezone().isoformat(timespec='seconds')}
    previous = next((r for r in reversed(read_jsonl_tail(root / 'feedback.jsonl', 1000))
                     if r.get('notification_id') == notification_id), {})
    record['acknowledged'] = action == 'acknowledged' or previous.get('acknowledged', previous.get('action') == 'acknowledged')
    review = previous.get('review_action') or (previous.get('action') if previous.get('action') != 'acknowledged' else None)
    if action == 'reopened':
        if not latest_actionable or latest_actionable.get('notification_id') != notification_id:
            raise ValueError('该事件已有新的提示，请刷新后再操作。')
        prior_review = review
        review = reopen_action(prior_review, previous)
        record.update(reopened_from=prior_review,
                      reopened_conclusion=previous.get('conclusion') or prior_review,
                      reopened_created_at=previous.get('created_at'))
    elif action != 'acknowledged':
        review = action
    if review:
        record['review_action'] = review
        # A later acknowledgement must not discard a saved review note.
        record['review_comment'] = comment.strip() if action != 'acknowledged' else previous.get('review_comment', previous.get('comment', ''))
        record['conclusion'] = ((previous.get('conclusion') or previous.get('review_action'))
            if action in ('resolved', 'acknowledged') else
            ('confirmed_abnormal' if review == 'confirmed_abnormal' else None) if action == 'reopened' else review)
        record['workflow_status'] = phase(review)
    else:
        record['workflow_status'] = 'pending'
    with (root / 'feedback.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    return record


def load_run(path):
    path = Path(path).resolve()
    summary = json.loads(read_json(path / 'summary.json'))
    config = json.loads(read_json(path / 'config.json')) if (path / 'config.json').is_file() else {}
    events = json.loads(read_json(path / 'events.json')).get('events', []) if (path / 'events.json').is_file() else []
    trend = json.loads(read_json(path / 'trend-latest.json')) if (path / 'trend-latest.json').is_file() else None
    return {'output': str(path), 'summary': summary, 'config': config, 'events': events,
            'notifications': read_jsonl_tail(path / 'alerts.jsonl'),
            'feedback': read_jsonl_tail(path / 'feedback.jsonl', 1000), 'trend': trend,
            'fusion': json.loads(read_json(path / 'fusion-latest.json')) if (path / 'fusion-latest.json').is_file() else None}


def list_runs(root):
    runs = []
    for folder in sorted(Path(root).glob('run-*'), reverse=True):
        if not (folder / 'summary.json').is_file():
            continue
        try:
            # History listing does not load videos or full frame logs.
            summary = json.loads(read_json(folder / 'summary.json'))
            config = json.loads(read_json(folder / 'config.json')) if (folder / 'config.json').is_file() else {}
            runs.append({'output': str(folder.resolve()), 'summary': summary, 'config': config})
        except (ValueError, OSError):
            continue
    return runs


def load_curve(path, interval_s=0.1, window_s=120.0):
    from collections import deque
    points = deque()
    fused_path = Path(path) / 'fusion.jsonl'
    use_fusion = fused_path.is_file()
    csv_path = fused_path if use_fusion else Path(path) / 'risk.csv'
    if not csv_path.is_file():
        return []
    last_time = -float('inf')
    with csv_path.open(encoding='utf-8', newline='') as stream:
        rows = (json.loads(line) for line in stream if line.strip()) if use_fusion else csv.DictReader(stream)
        for row in rows:
            timestamp = float(row['timestamp_s'])
            if timestamp - last_time + 1e-6 < interval_s:
                continue
            points.append((timestamp, float(row['score']) if row['status'] == 'assessed' else None))
            last_time = timestamp
            while points and points[0][0] < timestamp - window_s:
                points.popleft()
    return list(points)


def event_review_state(event_id, notifications, feedback):
    """Status of the latest actionable notice, so a new upgrade needs review."""
    latest = next((n for n in reversed(list(notifications))
                   if n.get('event_id') == event_id and n.get('kind') != 'event_closed'), None)
    if latest is None:
        return 'unavailable'
    record = feedback.get(latest.get('notification_id')) or {}
    return record.get('review_action') or record.get('action', 'pending')


def render_review_report(run_path):
    """Human-readable export; all events, latest notices and explicit feedback."""
    record = load_run(run_path)
    root = Path(run_path)
    latest_notices, feedback = {}, {}
    human_history = []
    def collect_feedback(row):
        if 'notification_id' in row:
            feedback[row['notification_id']] = row
            human_history.append(row)
    # Export uses complete files rather than the bounded UI display tails.
    for filename, visitor in (
        ('alerts.jsonl', lambda row: latest_notices.update({row['event_id']: row})
         if row.get('kind') != 'event_closed' and 'event_id' in row else None),
        ('feedback.jsonl', collect_feedback),
    ):
        path = root / filename
        if path.is_file() and not path.is_symlink():
            with path.open(encoding='utf-8') as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(item, dict):
                        visitor(item)

    def cell(value):
        return str(value).replace('|', '／').replace('\r', ' ').replace('\n', ' ')

    names = {'acknowledged': '已知悉', 'false_positive': '人工标为疑似误报',
             'needs_verification': '需进一步核实 · 待跟进', 'uncertain': '无法判断 · 待跟进',
             'normal_use': '人工判为正常用火', 'confirmed_abnormal': '人工确认异常 · 待处理',
             'resolved': '人工登记已处理', 'reopened': '已撤回 · 重新跟进', 'pending': '待核查', 'unavailable': '无提示记录'}
    summary = record['summary']
    status_names = {'completed': '分析完成', 'completed_with_frame_count_warning': '分析完成，视频信息需核对',
                    'stopped_by_user': '已手动停止', 'camera_disconnected': '摄像头连接中断',
                    'storage_limit_reached': '存储不足，检测已停止', 'failed': '分析失败'}
    lines = ['# 焰鉴 · 事件复核报告', '',
             f'输入：{cell(Path(record["config"].get("source", "未知")).name)}',
             f'实际检测方案：{cell(record["config"].get("model_profile_name", record["config"].get("model_profile", "未记录")))}',
             f'选择用途：{cell((record["config"].get("scene_selection") or {}).get("scene", "旧记录未记录选择用途"))}',
             f'分析状态：{cell(status_names.get(summary.get("status"), summary.get("status", "未知")))}；处理帧数：{summary.get("processed_frames", 0)}',
             '', ('时间采用电脑接收画面的相对时间；录像须配套时间索引回放；仅保存已处理帧。'
                  if record['config'].get('source_kind') == 'camera' else '时间采用源视频时间；') +
             '结束是软件事件边界，不代表实际火情结束。',
             '已知悉仅表示已查看，不代表核查完成；需进一步核实、无法判断均保留待跟进状态。新的等级变化需要重新核查。疑似误报是人工反馈，不是真值。', '',
             '| 事件 | 检测类型 | 首次检测 / 秒 | 持续特征确认 / 秒 | 检测结束 / 秒 | 核查优先程度 | 人工核查 |',
             '| --- | --- | --- | --- | --- | --- | --- |']
    images = []
    for event in record['events']:
        identifier = event['event_id']
        risk = event.get('fusion_summary') or event.get('risk_summary') or {}
        state = event_review_state(identifier, latest_notices.values(), feedback)
        values = (f'#{identifier}', {'fire': '火焰', 'smoke': '烟雾'}.get(event.get('label'), '未知'),
                  event.get('started_at_s', '—'), event.get('confirmed_at_s', '—'),
                  event.get('closed_at_s', '—'), LEVEL_NAMES.get(risk.get('peak_level'), '—'), names.get(state, state))
        lines.append('| ' + ' | '.join(cell(v) for v in values) + ' |')
        image = event.get('evidence_image')
        if image:
            # Link only allowed local evidence; never embed arbitrary source paths.
            candidate = (root / image).resolve()
            if ((root.resolve() / 'evidence') == candidate.parent and candidate.is_file()
                    and not candidate.is_symlink() and candidate.suffix.lower() in ('.jpg', '.png')):
                images.append(f'- 事件 #{identifier}：[查看确认截图]({candidate.relative_to(root.resolve()).as_posix()})')
    if not record['events']:
        lines.append('| — | 无确认事件 | — | — | — | — | — |')
    if images:
        lines.extend(['', '## 确认截图', ''] + images)
    if summary.get('scene_fusion'):
        fused = summary['scene_fusion']
        raw = summary.get('risk') or {}
        lines.extend(['', '## 综合研判结果' if fused.get('policy') == 'scene-score-v2' else '## 多模态融合结果', '',
            f'检测原始峰值：{cell(raw.get("peak_score", "—"))}；融合峰值：{cell(fused.get("peak_score", "—"))}。',
            '事件表采用融合后的峰值等级；risk.jsonl 保留原始检测指数，fusion.jsonl 记录逐帧最终等级和场景取帧时间。',
            'AI未知、过期或不支持修正时沿用检测等级；未确认事件的场景描述不作为已确认火灾。'])
        if fused.get('policy') == 'scene-score-v2':
            lines.append('采用显式动态规则形成一个综合核查指数；参数未通过小样本拟合，指数不是火灾概率。')
    scene_notice = latest_notices.get(None)
    if scene_notice and scene_notice.get('origin') == 'ai_scene':
        scene_state = event_review_state(None, latest_notices.values(), feedback)
        from core.review_workflow import presentation
        lines.extend(['', '## 综合异常提示核查', '',
            f'综合指数：{cell(scene_notice.get("score", "—"))}；提示等级：{cell(scene_notice.get("level", "—"))}。',
            f'核查状态：{cell(presentation(scene_state)["label"])}。',
            f'画面依据：{cell(scene_notice.get("description", ""))}',
            '该提示来自场景异常线索，尚无烟火检测确认事件，不计入YOLO确认事件数。'])
    comments = [(f'事件 #{row["event_id"]}' if row.get('event_id') is not None else '综合异常提示') + f'：{cell(row.get("review_comment") or row.get("comment"))}'
                for row in feedback.values() if row.get('review_comment') or row.get('comment')]
    if comments:
        lines.extend(['', '## 人工处理说明', ''] + ['- ' + text for text in comments])
    if human_history:
        from core.review_workflow import presentation, conclusion_label, CONCLUSION_NAMES, history_action_label
        lines.extend(['', '## 核查与处理记录', '', '| 时间 | 事件 | 操作 | 状态 | 核查结论 | 备注 |',
                      '| --- | --- | --- | --- | --- | --- |'])
        for row in human_history:
            action = row.get('action', '')
            status = presentation(row.get('review_action') or action)
            title = history_action_label(row)
            scope = f'#{row["event_id"]}' if row.get('event_id') is not None else '综合异常提示'
            note = row.get('review_comment') or row.get('comment', '')
            if action == 'reopened' and row.get('reopened_conclusion'):
                note = (note + '；' if note else '') + '撤回前结论：' + CONCLUSION_NAMES.get(row['reopened_conclusion'], row['reopened_conclusion'])
            values = (row.get('created_at', ''), scope, title, status['label'],
                      conclusion_label(row.get('review_action') or action, row), note)
            lines.append('| ' + ' | '.join(cell(value) for value in values) + ' |')
    if summary.get('warnings'):
        lines.extend(['', '## 记录说明', ''] + ['- ' + cell(item) for item in summary['warnings']])
    selection = record['config'].get('scene_selection') or {}
    if selection.get('tradeoff'):
        lines.extend(['', '## 检测方案说明', '', cell(selection['tradeoff']), cell(selection.get('validation', ''))])
    lines.extend(['', '回放：完整录像为 annotated.mp4；事件片段位于 clips/，索引见 clips.json（如存在）。原视频及权重未打包。',
                  '趋势是检测框覆盖的外推；指数不是火灾概率；检测次数不代表实测电量。', ''])
    point = record['config'].get('monitoring_point') or {}
    if point:
        lines.extend(['', '## 监控点', '', f'设备：{cell(point.get("name", "未登记"))}',
                      f'位置：{cell(point.get("location", "未登记"))}'])
    scene_records = read_jsonl_tail(root / 'scene-reviews.jsonl', 500)
    if scene_records:
        lines.extend(['', '## AI场景复核', '', '以下为本机模型意见与实验性核查指数修正，原始检测提示保留。'])
        for scene_record in scene_records:
            scene = scene_record.get('scene') or {}
            priority = scene_record.get('priority') or {}
            moment = '本轮结束时' if scene_record.get('record_kind') == 'end_of_run_context' else 'AI取帧时'
            scope = '视频场景（无需事件触发）' if scene_record.get('event_id') is None else f'事件 #{cell(scene_record["event_id"])}'
            lines.extend(['', f'### {scope}',
                          f'画面描述：{cell(scene.get("description", "未记录"))}',
                          f'模型：{cell(scene_record.get("model", "未记录"))}；耗时：{cell(scene_record.get("elapsed_s", "—"))}秒',
                          f'{moment}核查指数：{cell(priority.get("baseline_score", "—"))} → {cell(priority.get("score", "—"))}；{cell(priority.get("reason", ""))}',
                          f'不确定处：{cell("；".join(scene.get("uncertainties", [])))}',
                          f'待核对：{cell(scene.get("next_check", ""))}'])
            for item in scene_record.get('evidence', []):
                relative = item.get('image', '')
                if re.fullmatch(r'scene-evidence/(?:event-\d+|scene)-[0-9a-f]{20}\.jpg', relative):
                    path = root / relative
                    if path.is_file() and not path.is_symlink() and path.resolve().parent == (root / 'scene-evidence').resolve():
                        lines.append(f'- 原画面 {cell(item.get("timestamp_s"))}秒：[查看证据]({relative})')
    return '\n'.join(lines)


def export_run(run_path, destination):
    """Atomic ZIP, fixed allowlist, relative entries; no model or source video copy."""
    root = Path(run_path).resolve()
    destination = Path(destination).resolve()
    if root == destination or root in destination.parents:
        raise ValueError('请将导出包保存到本次结果目录之外。')
    if not (root / 'summary.json').is_file():
        raise ValueError('分析尚未结束，不能导出未完成的记录。')
    allowed = {'annotated.mp4', 'detections.jsonl', 'temporal.jsonl', 'risk.jsonl', 'risk.csv',
               'events.json', 'risk-events.json', 'config.json', 'summary.json', 'trend.jsonl',
               'trend-latest.json', 'monitoring.jsonl', 'alerts.jsonl', 'feedback.jsonl', 'frame-times.jsonl',
               'clips.json', 'protected.json', 'scene-reviews.jsonl', 'fusion.jsonl', 'fusion-latest.json', 'fusion-events.json'}
    files = [p for p in root.iterdir() if p.name in allowed and p.is_file() and not p.is_symlink()]
    evidence = root / 'evidence'
    if evidence.is_dir() and not evidence.is_symlink():
        files.extend(p for p in evidence.iterdir() if p.is_file() and not p.is_symlink() and p.suffix.lower() in ('.jpg', '.png'))
    clips = root / 'clips'
    if clips.is_dir() and not clips.is_symlink() and clips.resolve() == clips:
        files.extend(p for p in clips.iterdir() if p.is_file() and not p.is_symlink()
                     and re.fullmatch(r'clip-\d+\.(mp4|jsonl)', p.name) and p.resolve().parent == clips)
    scene_folder = root / 'scene-evidence'
    if scene_folder.is_dir() and not scene_folder.is_symlink() and scene_folder.resolve() == scene_folder:
        files.extend(p for p in scene_folder.iterdir() if p.is_file() and not p.is_symlink()
                     and re.fullmatch(r'(?:event-\d+|scene)-[0-9a-f]{20}\.jpg', p.name) and p.resolve().parent == scene_folder)
    fd, temporary = tempfile.mkstemp(prefix='fireguard-export-', suffix='.tmp', dir=str(destination.parent))
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for path in sorted(files):
                archive.write(path, path.relative_to(root).as_posix())
            archive.writestr('事件复核报告.md', render_review_report(root))
            archive.writestr('README.txt', '焰鉴分析记录\n时间以源视频为准；风险指数不是火灾概率。\n'
                             '摄像头记录例外：时间以电脑接收画面的相对时间为准；完整录像或事件片段须配套时间索引，仅保存已处理帧。\n'
                             '事件结束是软件边界，不代表实际火情结束。\n'
                             '趋势预测为检测框覆盖的短期外推；节能数据是检测次数，不是电量测量。\n'
                             'observed=false 表示该帧未检测，不能作为无火无烟真值；反馈不修改标注或模型。\n'
                             '包含参数、模型来源、日志、事件和输出视频；原视频及模型权重未打包。\n'
                             '配置中的输入路径是原机器路径；运行复现需要合法取得对应素材与模型。\n')
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(destination)
