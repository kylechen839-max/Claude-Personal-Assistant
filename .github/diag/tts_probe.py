"""Temporary CI probe: which Windows TTS paths produce audio on the runner?"""
import os
import subprocess
import tempfile

d = tempfile.mkdtemp()


def size(p):
    return os.path.getsize(p) if os.path.exists(p) else -1


def run(name, fn):
    p = os.path.join(d, name + ".wav")
    try:
        fn(p)
        print(name, size(p))
    except Exception as e:
        print(name, "ERROR", repr(e))


def via_init(p):
    import pyttsx3
    e = pyttsx3.init()
    e.save_to_file("Hello Kyle, this is a test.", p)
    e.runAndWait()


def via_engine(p):
    from pyttsx3.engine import Engine
    e = Engine()
    e.save_to_file("Hello Kyle, this is a test.", p)
    e.runAndWait()


def via_comtypes(p):
    import comtypes.client
    voice = comtypes.client.CreateObject("SAPI.SpVoice")
    stream = comtypes.client.CreateObject("SAPI.SpFileStream")
    stream.Open(p, 3)
    voice.AudioOutputStream = stream
    voice.Speak("Hello Kyle, this is a test.", 0)
    stream.Close()


def via_dotnet(p):
    ps = ("Add-Type -AssemblyName System.Speech; "
          "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.SetOutputToWaveFile('{p}'); $s.Speak('Hello Kyle, this is a test.'); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


for name, fn in [("pyttsx3_init", via_init), ("pyttsx3_engine", via_engine),
                 ("comtypes_sapi", via_comtypes), ("dotnet_speech", via_dotnet)]:
    run(name, fn)
