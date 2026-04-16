"""Generate Slouchy voice phrases locally using F5-TTS.

F5-TTS (2024) generally clones voice timbre more faithfully than XTTS v2,
especially for voices outside the XTTS training distribution. Uses Whisper
to auto-transcribe the reference audio (F5-TTS needs both the clip AND
its transcript as conditioning).

First run downloads the F5-TTS model (~1.3 GB) and a Whisper model (~75 MB).
"""

import os
import sys

import soundfile as sf
import torch
import whisper
from f5_tts.api import F5TTS

VOICE_SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "voice_samples")
PHRASES_DIR = os.path.join(os.path.dirname(__file__), "phrases")

# F5-TTS takes ONE reference clip + its transcript. Use a single clean ~8s clip.
REF_FILE = os.path.join(VOICE_SAMPLES_DIR, "ref_1.wav")

PHRASES = {
    "tier1_gentle": [
        ("gentle_01.wav", "Arre beta, you are slouching again. Sit up straight, please."),
        ("gentle_02.wav", "What is this posture, haan? Come on, straighten up nicely."),
        ("gentle_03.wav", "Beta, your back is not a banana. Sit up nice and tall, please."),
        ("gentle_04.wav", "Why are you curving like that? Fix your posture quickly, nah."),
        ("gentle_05.wav", "How many times, beta? Sit up straight. This is not correct."),
    ],
    "tier2_firm": [
        ("firm_01.wav", "Okay, that is enough! I already told you nicely. Sit up straight right now!"),
        ("firm_02.wav", "How many times do I have to tell you, haan? You are not listening to me at all!"),
        ("firm_03.wav", "Arre, what is this behaviour? Sit up properly this instant! I am serious!"),
        ("firm_04.wav", "You are looking like a melted pakora! Straighten your back, right now!"),
        ("firm_05.wav", "Bas! That is enough slouching. Fix it immediately, understand?"),
    ],
    "tier3_nuclear": [
        ("nuclear_01.wav", "Hey bhagwan! You have been slouching for five full minutes! Sit up right now or I am coming through this screen!"),
        ("nuclear_02.wav", "What nonsense! A prawn has better posture than you! Sharam karo and sit up immediately, I said!"),
        ("nuclear_03.wav", "That is it! I have been watching you become a melted roti and I am not tolerating this anymore! Sit. Up. Now!"),
    ],
}


def transcribe(audio_path: str) -> str:
    """Transcribe reference audio with Whisper (base model, en-only)."""
    print("  Transcribing reference with Whisper...")
    model = whisper.load_model("base.en")
    result = model.transcribe(audio_path, fp16=False)
    text = result["text"].strip()
    print(f"  Reference transcript: {text[:80]}{'...' if len(text) > 80 else ''}")
    return text


def main():
    if not os.path.exists(REF_FILE):
        print(f"Missing reference voice: {REF_FILE}")
        sys.exit(1)

    ref_text = transcribe(REF_FILE)

    print("\nLoading F5-TTS (downloads ~1.3 GB on first run)...")
    device = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"  Device: {device}")
    f5 = F5TTS(device=device)
    print("  Model loaded.")

    total = sum(len(p) for p in PHRASES.values())
    done = 0
    for tier_dir, phrases in PHRASES.items():
        out_dir = os.path.join(PHRASES_DIR, tier_dir)
        os.makedirs(out_dir, exist_ok=True)

        print(f"\nGenerating {tier_dir}...")
        for filename, text in phrases:
            filepath = os.path.join(out_dir, filename)
            done += 1
            print(f"  [{done}/{total}] {filename}")

            # Higher cfg_strength forces closer adherence to the reference
            # (preserves accent, timbre, cadence more faithfully); 3.0 is
            # the sweet spot before artifacts creep in.
            # nfe_step=48 improves audio quality over the default 32.
            wav, sr, _ = f5.infer(
                ref_file=REF_FILE,
                ref_text=ref_text,
                gen_text=text,
                remove_silence=True,
                cfg_strength=3.0,
                nfe_step=48,
                speed=0.95,
            )
            sf.write(filepath, wav, sr)
            size_kb = os.path.getsize(filepath) / 1024
            print(f"    Saved ({size_kb:.0f} KB)")

    print(f"\nDone! {total} phrases regenerated.")


if __name__ == "__main__":
    main()
