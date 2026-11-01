"""STT diagnostics keep actionable phases but never leak provider error bodies."""
import builtins
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from backend.app.ai import AIError, _transcribe_local, local_transcription_error


SECRET = 'secret-do-not-show-7fe26'
SIGNED = f'https://storage.invalid/model.bin?token={SECRET}&signature=private'


def native_error(code):
    error = OSError(SIGNED)
    error.winerror = code
    return error


def with_cause(error):
    outer = RuntimeError(f'failed request {SIGNED}')
    outer.__cause__ = error
    return outer


def http_error(status):
    error = RuntimeError(SIGNED)
    error.response = SimpleNamespace(status_code=status)
    return with_cause(error)


@pytest.mark.parametrize(('error', 'code'), [
    (OSError(28, SIGNED), 'DISK'),
    (native_error(112), 'DISK'),
    (PermissionError(13, SIGNED), 'PERMISSION'),
    (native_error(5), 'PERMISSION'),
    (native_error(206), 'PATH'),
    (MemoryError(SIGNED), 'MEMORY'),
    (native_error(1455), 'MEMORY'),
    (RuntimeError(f'std::bad_alloc {SIGNED}'), 'MEMORY'),
    (http_error(401), 'ACCESS'),
    (http_error(403), 'ACCESS'),
    (http_error(429), 'RATE_LIMIT'),
    (http_error(404), 'MODEL_NOT_FOUND'),
    (http_error(503), 'SERVICE'),
    (RuntimeError(f'reqwest certificate verify failed {SIGNED}'), 'TLS'),
    (RuntimeError(f'Xet CAS service HTTP status client error (403 Forbidden) {SIGNED}'), 'ACCESS'),
    (RuntimeError(f'Xet CAS service failed to connect {SIGNED}'), 'NETWORK'),
    (TimeoutError(SIGNED), 'TIMEOUT'),
    (native_error(126), 'NATIVE'),
    (native_error(127), 'NATIVE'),
    (native_error(193), 'NATIVE'),
    (RuntimeError(f'cublas64 cannot load library {SIGNED}'), 'COMPUTE'),
    (RuntimeError(f'Requested float16 compute type {SIGNED}'), 'COMPUTE'),
    (RuntimeError(f'Requested int8 compute type, target does not support efficient int8 computation {SIGNED}'), 'COMPUTE_INT8'),
    (RuntimeError(f'unsupported instruction {SIGNED}'), 'CPU'),
    (ImportError(f'cannot import name from package {SIGNED}'), 'DEPENDENCY_IMPORT'),
    (RuntimeError(f'checksum mismatch {SIGNED}'), 'MODEL_FILES'),
])
def test_local_stt_errors_are_safe_actionable_and_preserve_phase(error, code):
    result = str(local_transcription_error(with_cause(error), 'load', 'base'))
    assert f'[STT_LOAD_{code}]' in result
    assert 'base' in result
    assert 'загрузка модели' in result
    assert SECRET not in result
    assert 'storage.invalid' not in result
    assert 'signature=' not in result


def test_httpx2_error_type_is_classified_through_wrapped_context():
    connect_error = type('ConnectError', (Exception,), {'__module__': 'httpx2'})
    underlying = connect_error(SIGNED)
    wrapper = RuntimeError(SIGNED)
    wrapper.__context__ = underlying
    result = str(local_transcription_error(wrapper, 'load', 'base'))
    assert '[STT_LOAD_NETWORK]' in result
    assert SECRET not in result


def test_cycle_in_exception_chain_and_untrusted_model_name_are_safe():
    error = RuntimeError(SIGNED)
    error.__cause__ = error
    result = str(local_transcription_error(error, 'inference', SIGNED))
    assert '[STT_INFERENCE_UNKNOWN]' in result
    assert 'RuntimeError' in result
    assert SECRET not in result
    assert 'storage.invalid' not in result


def test_vad_failure_has_its_own_advice_not_no_speech_claim():
    vad_error = type('Fail', (Exception,), {'__module__': 'onnxruntime.capi.onnxruntime_pybind11_state'})
    result = str(local_transcription_error(vad_error(SIGNED), 'inference', 'base'))
    assert '[STT_INFERENCE_VAD]' in result
    assert 'не означает' in result
    assert SECRET not in result


def test_actual_missing_package_is_distinguished_from_native_import_error(monkeypatch):
    original = builtins.__import__
    def import_module(name, *args, **kwargs):
        if name == 'faster_whisper':
            raise ModuleNotFoundError(SIGNED, name='faster_whisper')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', import_module)
    with pytest.raises(AIError) as result:
        _transcribe_local(Path('unused.wav'), {'whisper_model':'base'}, 5, None)
    assert '[STT_IMPORT_PACKAGE]' in str(result.value)
    assert SECRET not in str(result.value)


def test_missing_onnx_is_dependency_instead_of_missing_whisper():
    error = ModuleNotFoundError(SIGNED, name='onnxruntime')
    result = str(local_transcription_error(error, 'inference', 'base'))
    assert '[STT_INFERENCE_DEPENDENCY]' in result
    assert 'onnxruntime' in result
    assert 'faster-whisper отсутствует' not in result
    assert SECRET not in result


def fake_whisper(monkeypatch, constructor):
    module = ModuleType('faster_whisper')
    module.WhisperModel = constructor
    monkeypatch.setitem(sys.modules, 'faster_whisper', module)


def test_model_initialization_failure_is_load_phase_and_does_not_fallback(monkeypatch):
    calls = []
    def constructor(name, **kwargs):
        calls.append(name)
        raise MemoryError(SIGNED)
    fake_whisper(monkeypatch, constructor)
    with pytest.raises(AIError) as result:
        _transcribe_local(Path('unused.wav'), {'whisper_model':'large-v3'}, 5, None)
    assert '[STT_LOAD_MEMORY]' in str(result.value)
    assert calls == ['large-v3']
    assert result.value.__suppress_context__ is True
    assert SECRET not in str(result.value)


def test_lazy_inference_failure_does_not_return_partial_transcript(monkeypatch):
    class Model:
        def __init__(self, *args, **kwargs):
            pass
        def transcribe(self, *args, **kwargs):
            def segments():
                yield SimpleNamespace(words=[SimpleNamespace(start=0, end=1, word='hello')], end=1)
                raise native_error(126)
            return segments(), None
    fake_whisper(monkeypatch, Model)
    with pytest.raises(AIError) as result:
        _transcribe_local(Path('unused.wav'), {'whisper_model':'base'}, 5, None)
    assert '[STT_INFERENCE_NATIVE]' in str(result.value)
    assert SECRET not in str(result.value)


def test_success_keeps_word_timestamps_and_progress_contract(monkeypatch):
    class Model:
        def __init__(self, model, **kwargs):
            assert model == 'base'
        def transcribe(self, path, **kwargs):
            assert kwargs['word_timestamps'] is True
            assert kwargs['vad_filter'] is True
            return iter([SimpleNamespace(words=[SimpleNamespace(start=.1,end=.9,word=' hello')],end=1)]), None
    fake_whisper(monkeypatch, Model)
    progress = []
    result = _transcribe_local(Path('unused.wav'), {'whisper_model':'base'}, 2, progress.append)
    assert result == [{'start':.1, 'end':.9, 'text':' hello'}]
    assert progress == [.5]
