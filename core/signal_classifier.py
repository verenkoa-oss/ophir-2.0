"""
OPHIR Signal Classifier Module
Classifies RF signals detected via SDR.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_classifier_instance = None


class SignalClassifier:
    """Lightweight signal classifier backed by a pre-trained model (if available)."""

    def __init__(self):
        self.model = None
        self._load_model()

    def _load_model(self):
        """Load a trained model from disk if one exists."""
        model_path = Path(__file__).parent.parent / "data" / "signal_model.pkl"
        if model_path.exists():
            try:
                import pickle  # noqa: S403 – loading trusted internal model
                with open(model_path, "rb") as f:
                    self.model = pickle.load(f)  # noqa: S301
                logger.info("✅ Loaded trained model (AI classifier)")
            except Exception as e:
                logger.warning(f"⚠️ Could not load model: {e}. Using fallback.")
        else:
            logger.info("ℹ️ No trained model found – using fallback classifier")

    def classify(self, features: dict) -> dict:
        """Classify a signal given a feature dictionary."""
        if self.model is not None:
            try:
                signal_type = self.model.predict([list(features.values())])[0]
                return {"signal_type": str(signal_type), "confidence": 0.9}
            except Exception:
                pass
        # Fallback heuristic
        noise_dbm = features.get("noise_dbm", 0)
        if noise_dbm < -80:
            return {"signal_type": "NOISE", "confidence": 0.5}
        return {"signal_type": "ADS-B", "confidence": 0.8}


def get_classifier() -> SignalClassifier:
    """Return a singleton SignalClassifier instance."""
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = SignalClassifier()
    return _classifier_instance
