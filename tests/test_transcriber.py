import sys
import types
import unittest
from unittest.mock import Mock, patch

import transcriber


class TranscriberResourceTests(unittest.TestCase):
    def test_model_uses_one_cpu_worker(self):
        constructor = Mock(return_value=object())
        dependency = types.ModuleType("faster_whisper")
        dependency.WhisperModel = constructor
        with patch.object(transcriber, "_model", None), patch.dict(sys.modules, {"faster_whisper": dependency}):
            model = transcriber._get_model("base")
            self.assertIs(model, constructor.return_value)
            constructor.assert_called_once_with("base", device="cpu", compute_type="int8", cpu_threads=1)

    def test_model_instance_is_reused(self):
        cached = object()
        dependency = types.ModuleType("faster_whisper")
        dependency.WhisperModel = Mock()
        with patch.object(transcriber, "_model", cached), patch.dict(sys.modules, {"faster_whisper": dependency}):
            self.assertIs(transcriber._get_model("base"), cached)
            dependency.WhisperModel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
