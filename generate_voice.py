"""Generate Slouchy voice phrases with ElevenLabs.

Supports three voice-selection modes:
1) Explicit voice ID override (best for free-plan default voices)
2) Instant voice clone from local sample (if plan allows)
3) Built-in fallback default voice
"""

import os
import sys
import wave

from elevenlabs.client import ElevenLabs

API_KEY = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ELEVENLABS_API_KEY")
VOICE_ID_OVERRIDE = (
    sys.argv[2]
    if len(sys.argv) > 2
    else os.environ.get("ELEVENLABS_VOICE_ID")
)
PHRASE_PRESET = (
    sys.argv[3]
    if len(sys.argv) > 3
    else os.environ.get("SLOUCHY_PHRASE_PRESET", "english_fun")
)

VOICE_SAMPLE = os.path.join(os.path.dirname(__file__), "voice_samples", "voice_sample.wav")
PHRASES_DIR = os.path.join(os.path.dirname(__file__), "phrases")

PHRASE_PRESETS = {
    "english_fun": {
        "tier1_gentle": [
            ("gentle_01.wav", "Posture check, superstar. Sit tall before your spine files a complaint."),
            ("gentle_02.wav", "Tiny shrimp alert. Un-shrimp yourself and sit up straight."),
            ("gentle_03.wav", "Your back is doing modern art. Let's switch to classic straight posture."),
            ("gentle_04.wav", "Friendly reminder: shoulders back, chin up, main character energy on."),
            ("gentle_05.wav", "Sit tall for ten seconds. Your future self just sent a thank-you note."),
        ],
        "tier2_firm": [
            ("firm_01.wav", "Okay, comedy is over. Sit up straight right now."),
            ("firm_02.wav", "You are folding like a lawn chair. Back straight, immediately."),
            ("firm_03.wav", "That question-mark posture is not a personality trait. Fix it now."),
            ("firm_04.wav", "You are slowly becoming desk-shaped. Undo that right this second."),
            ("firm_05.wav", "I asked nicely already. Shoulders back, spine tall, now."),
        ],
        "tier3_nuclear": [
            ("nuclear_01.wav", "Emergency posture broadcast. You have been slouching for five minutes. Sit up right now."),
            ("nuclear_02.wav", "This is a spine intervention. Even your chair is worried. Straighten up immediately."),
            ("nuclear_03.wav", "Final warning from posture headquarters. Sit tall now or I start yelling in surround sound."),
        ],
    },
    "hinglish_fun": {
        "tier1_gentle": [
            ("gentle_01.wav", "Arre posture check, beta. Seedha baitho, back ko pyaar do."),
            ("gentle_02.wav", "Oho, chhota shrimp mode on ho gaya. Sit up straight, jaldi."),
            ("gentle_03.wav", "Spine ko banana mat banao. Tall baitho, hero lagoge."),
            ("gentle_04.wav", "Screen interesting hai, lekin posture aur important hai. Shoulders back."),
            ("gentle_05.wav", "Friendly nudge: seedha baitho for ten seconds. Bas itna hi."),
        ],
        "tier2_firm": [
            ("firm_01.wav", "Theek hai, masti khatam. Abhi ke abhi seedha baitho."),
            ("firm_02.wav", "Kitni baar bolun? Lawn chair ki tarah fold mat ho."),
            ("firm_03.wav", "Question mark posture band karo. Back straight, right now."),
            ("firm_04.wav", "Aise lag raha hai chair mein pighal rahe ho. Seedha baitho."),
            ("firm_05.wav", "Nice mode khatam. Shoulders back, chest up, immediately."),
        ],
        "tier3_nuclear": [
            ("nuclear_01.wav", "Breaking news: five minute se slouch chal raha hai. Seedha baitho abhi."),
            ("nuclear_02.wav", "Yeh posture nahi, public emergency hai. Chair bhi tension mein hai. Sit up now."),
            ("nuclear_03.wav", "Final warning from posture police. Abhi seedha baitho warna full volume lecture."),
        ],
    },
}


def main():
    if not API_KEY:
        print("Usage: python generate_voice.py <ELEVENLABS_API_KEY> [VOICE_ID] [PHRASE_PRESET]")
        print("Env vars: ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, SLOUCHY_PHRASE_PRESET")
        sys.exit(1)
    if PHRASE_PRESET not in PHRASE_PRESETS:
        choices = ", ".join(sorted(PHRASE_PRESETS))
        print(f"Unknown phrase preset: {PHRASE_PRESET}")
        print(f"Available presets: {choices}")
        sys.exit(1)

    client = ElevenLabs(api_key=API_KEY)
    phrases = PHRASE_PRESETS[PHRASE_PRESET]
    print(f"Using phrase preset: {PHRASE_PRESET}")

    # Voice selection priority:
    # 1) Explicit voice ID override
    # 2) IVC clone (if available)
    # 3) Fallback default voice
    voice_id = None
    if VOICE_ID_OVERRIDE:
        voice_id = VOICE_ID_OVERRIDE
        print(f"Using voice override: {voice_id}")
    else:
        try:
            print("Attempting voice cloning from YouTube sample...")
            with open(VOICE_SAMPLE, "rb") as f:
                voice = client.voices.ivc.create(
                    name="Slouchy Mom Voice",
                    description="Voice cloned from user's YouTube channel for posture scolding",
                    files=[f],
                )
            voice_id = voice.voice_id
            print(f"  Voice cloned! ID: {voice_id}")
        except Exception as e:
            print(f"  Voice cloning unavailable ({e.__class__.__name__}), using default voice.")
            # Sarah - Mature, Reassuring, Confident
            voice_id = "EXAVITQu4vr4xnSDxMaL"
            print(f"  Using fallback voice: {voice_id}")

    # Step 2: Generate phrases for each tier
    for tier_dir, tier_phrases in phrases.items():
        out_dir = os.path.join(PHRASES_DIR, tier_dir)
        os.makedirs(out_dir, exist_ok=True)

        print(f"\nGenerating {tier_dir} phrases...")
        for filename, text in tier_phrases:
            filepath = os.path.join(out_dir, filename)
            print(f"  → {filename}")

            audio_iter = client.text_to_speech.convert(
                voice_id=voice_id,
                text=text,
                model_id="eleven_multilingual_v2",
                output_format="pcm_22050",
            )

            # Collect all PCM chunks
            pcm_data = b"".join(audio_iter)

            # Write as WAV (PCM 16-bit mono 22050Hz)
            with wave.open(filepath, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(22050)
                wav_file.writeframes(pcm_data)

            print(f"    Saved ({os.path.getsize(filepath) / 1024:.0f} KB)")

    print(f"\nDone! Generated {sum(len(p) for p in phrases.values())} phrases.")
    print(f"Voice ID: {voice_id} (saved in ElevenLabs account)")


if __name__ == "__main__":
    main()
