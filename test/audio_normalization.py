import array
import ctypes as c
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import wave

if sys.version_info < (3, 12):
    raise SystemExit('Python 3.12+ is required to read the PCM WAV output.')

root = Path(sys.argv[1]).resolve()
loaded = {}
def library_path(name):
    framework = name.split('/')[0]
    return root / framework.replace('.framework', '.xcframework') / 'macos-arm64_x86_64' / name

def load(path):
    path = path.resolve()
    if path in loaded:
        return loaded[path]
    for line in subprocess.check_output(['otool', '-L', str(path)], text=True).splitlines()[1:]:
        name = line.strip().split(' (')[0]
        if name.startswith('@rpath/'):
            dependency = library_path(name.removeprefix('@rpath/')).resolve()
            if dependency != path:
                load(dependency)
    loaded[path] = c.CDLL(str(path))
    return loaded[path]

mpv = load(library_path('Mpv.framework/Versions/A/Mpv'))
mpv.mpv_create.restype = c.c_void_p
for name in ['mpv_initialize', 'mpv_terminate_destroy']:
    getattr(mpv, name).argtypes = [c.c_void_p]
mpv.mpv_set_option_string.argtypes = [c.c_void_p, c.c_char_p, c.c_char_p]
mpv.mpv_set_property_string.argtypes = [c.c_void_p, c.c_char_p, c.c_char_p]
mpv.mpv_command.argtypes = [c.c_void_p, c.POINTER(c.c_char_p)]
mpv.mpv_get_property_string.argtypes = [c.c_void_p, c.c_char_p]
mpv.mpv_get_property_string.restype = c.c_void_p
mpv.mpv_free.argtypes = [c.c_void_p]
mpv.mpv_request_log_messages.argtypes = [c.c_void_p, c.c_char_p]
mpv.avfilter_get_by_name.argtypes = [c.c_char_p]
mpv.avfilter_get_by_name.restype = c.c_void_p

class Event(c.Structure):
    _fields_ = [('id', c.c_int), ('error', c.c_int), ('userdata', c.c_ulonglong), ('data', c.c_void_p)]
class Log(c.Structure):
    _fields_ = [('prefix', c.c_char_p), ('level', c.c_char_p), ('text', c.c_char_p), ('loglevel', c.c_int)]
mpv.mpv_wait_event.argtypes = [c.c_void_p, c.c_double]
mpv.mpv_wait_event.restype = c.POINTER(Event)
FILTER = 'lavfi=[dynaudnorm=f=150:g=7:p=0.9:m=5]'
for name in ['dynaudnorm', 'acompressor', 'alimiter', 'loudnorm', 'aresample', 'aformat']:
    assert mpv.avfilter_get_by_name(name.encode()), 'Missing filter: ' + name

def command(handle, *values):
    args = (c.c_char_p * (len(values) + 1))(*[value.encode() for value in values], None)
    return mpv.mpv_command(handle, args)

def property_value(handle, name):
    value = mpv.mpv_get_property_string(handle, name.encode())
    if not value:
        return None
    try:
        return c.string_at(value).decode()
    finally:
        mpv.mpv_free(value)

with tempfile.TemporaryDirectory(prefix='kuraku-normalization-') as folder:
    source = Path(folder) / 'input.wav'
    samples = array.array('h', (int((1000 if i < 48000 * 4 else 18000) * math.sin(i * 2 * math.pi * 440 / 48000))
                                for i in range(48000 * 8)))
    with wave.open(str(source), 'wb') as audio:
        audio.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        audio.writeframes(array.array('h', (value for sample in samples for value in (sample, sample))).tobytes())
    outputs = []
    for index, filter_value in enumerate(['', FILTER]):
        target = Path(folder) / f'output-{index}.wav'
        handle = mpv.mpv_create()
        errors = []
        try:
            for name, value in {'config': 'no', 'ao': 'pcm', 'ao-pcm-file': str(target), 'audio-format': 's16',
                                'vo': 'null', 'vid': 'no', 'idle': 'yes', 'terminal': 'no'}.items():
                assert mpv.mpv_set_option_string(handle, name.encode(), value.encode()) >= 0, name
            assert mpv.mpv_initialize(handle) >= 0
            mpv.mpv_request_log_messages(handle, b'error')
            assert command(handle, 'af', 'set', filter_value) >= 0
            args = (c.c_char_p * 3)(b'loadfile', str(source).encode(), None)
            assert mpv.mpv_command(handle, args) >= 0
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                event = mpv.mpv_wait_event(handle, .1).contents
                if event.id == 2:
                    errors.append(c.cast(event.data, c.POINTER(Log)).contents.text.decode())
                if event.id == 7:
                    break
            else:
                raise AssertionError('Audio rendering timed out')
        finally:
            mpv.mpv_terminate_destroy(handle)
        assert not errors, errors
        with wave.open(str(target), 'rb') as audio:
            assert audio.getsampwidth() == 2 and audio.getnchannels() == 2
            output = array.array('h', audio.readframes(audio.getnframes()))
        assert len(output) >= 48000 * 2 * 7, 'Audio was truncated'
        quiet = output[48000 * 2:48000 * 2 * 3]
        rms = math.sqrt(sum(value * value for value in quiet) / len(quiet))
        assert rms > 0, 'Audio is silent'
        peak = max(abs(value) for value in output) / 32768
        assert peak < 0.99, 'Audio is clipped'
        outputs.append(rms)
        print(f'{"normalized" if filter_value else "off"}: quiet RMS={rms:.1f}, peak={peak:.3f}, samples={len(output)}')
    assert outputs[1] > outputs[0] * 1.5, 'Normalization did not lift quiet audio'
    print('PASS: bundled mpv supports normalization, renders non-silent audio, and lifts quiet sections without clipping.')

    handle = mpv.mpv_create()
    try:
        for name, value in {'config': 'no', 'ao': 'null', 'vo': 'null', 'vid': 'no', 'idle': 'yes', 'terminal': 'no'}.items():
            assert mpv.mpv_set_option_string(handle, name.encode(), value.encode()) >= 0
        assert mpv.mpv_initialize(handle) >= 0
        assert command(handle, 'loadfile', str(source)) >= 0
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and property_value(handle, 'current-ao') != 'null':
            mpv.mpv_wait_event(handle, .05)
        assert property_value(handle, 'current-ao') == 'null'
        for filter_value in [FILTER, '', FILTER]:
            assert command(handle, 'af', 'set', filter_value) >= 0
            assert ('dynaudnorm' in (property_value(handle, 'af') or '')) == bool(filter_value)
            assert command(handle, 'seek', '2', 'absolute+exact') >= 0
            deadline = time.monotonic() + .4
            while time.monotonic() < deadline:
                mpv.mpv_wait_event(handle, .05)
            assert property_value(handle, 'current-ao') == 'null', 'Audio output disappeared after toggle/seek'
        before = property_value(handle, 'af')
        assert command(handle, 'af', 'set', 'lavfi=[kuraku_missing_filter]') < 0
        assert property_value(handle, 'af') == before, 'Failed filter did not roll back'
        assert property_value(handle, 'current-ao') == 'null'
        print('PASS: toggle, seek and rejected-filter rollback keep audio output active.')
    finally:
        mpv.mpv_terminate_destroy(handle)

def verify_vr_projection():
    source = Path(__file__).parent / 'fixtures' / 'vr.mp4'
    assert mpv.avfilter_get_by_name(b'v360'), 'Missing filter: v360'
    handle = mpv.mpv_create()
    try:
        for name, value in {'config': 'no', 'ao': 'null', 'vo': 'null', 'pause': 'yes',
                            'terminal': 'no'}.items():
            assert mpv.mpv_set_option_string(handle, name.encode(), value.encode()) >= 0
        assert mpv.mpv_initialize(handle) >= 0
        mpv.mpv_request_log_messages(handle, b'error')
        assert command(handle, 'loadfile', str(source)) >= 0
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and property_value(handle, 'video-params/w') != '2048':
            mpv.mpv_wait_event(handle, .02)
        assert property_value(handle, 'video-params/w') == '2048', 'Video did not load'
        assert command(handle, 'vf', 'add', '@preserved:format=fmt=yuv420p') >= 0
        for projection in ['e', 'he']:
            for stereo in ['2d', 'sbs', 'tb']:
                for fov in [40, 90, 120]:
                    vertical = 2 * math.atan(math.tan(fov * math.pi / 360) * 9 / 16) * 180 / math.pi
                    value = f'@kuraku-vr360:lavfi=[v360=input={projection}:output=flat:in_stereo={stereo}:out_stereo=2d:w=1280:h=720:h_fov={fov}:v_fov={vertical}:yaw=20:pitch=10]'
                    assert command(handle, 'vf', 'add', value) >= 0, (projection, stereo, fov)
                    filters = property_value(handle, 'vf')
                    assert 'kuraku-vr360' in filters and 'preserved' in filters, filters
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        event = mpv.mpv_wait_event(handle, .02).contents
                        if event.id == 2:
                            raise AssertionError(c.cast(event.data, c.POINTER(Log)).contents.text.decode())
                        if property_value(handle, 'video-out-params/w') == '1280':
                            break
                    assert property_value(handle, 'video-out-params/w') == '1280'
                    assert property_value(handle, 'video-out-params/h') == '720'
        assert command(handle, 'vf', 'remove', '@kuraku-vr360') >= 0
        filters = property_value(handle, 'vf')
        assert 'kuraku-vr360' not in filters and 'preserved' in filters
        print('PASS: 18 VR projection/stereo/FOV combinations, view replacement and independent filter preservation.')
    finally:
        mpv.mpv_terminate_destroy(handle)

verify_vr_projection()
