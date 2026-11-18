"""Observe a development fusion model without changing operator notifications."""
from pathlib import Path


class FusionShadow:
    def __init__(self, model_path=None):
        self.model = None
        self.status = 'awaiting_model'
        self.error = ''
        if model_path:
            try:
                from core.adaptive_fusion import FusionModel
                self.model = FusionModel.load(Path(model_path))
                self.status = 'development'
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self.status = 'unavailable'
                self.error = str(exc)[:400]

    def metadata(self):
        return {'mode': 'shadow', 'status': self.status,
                'model_id': self.model.model_id if self.model else None,
                'affects_notifications': False, 'experimental': True,
                'calibrated': False, 'error': self.error}

    def update(self, assessment, record):
        result = self.metadata()
        if not self.model:
            return result
        rows = []
        for event in assessment['events']:
            try:
                prediction = self.model.predict(event, record,
                    timestamp_s=assessment['timestamp_s'], active_event_count=len(assessment['events']))
            except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
                prediction = {'status': 'skipped', 'score': event['score'], 'context_delta': 0.,
                              'reason': str(exc)[:400], 'experimental': True, 'calibrated': False}
            rows.append({'event_id': event['event_id'], **prediction})
        result['events'] = rows
        return result
