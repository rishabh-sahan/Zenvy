let recorder;
let audioChunks = [];
let isRecording = false;

const recordButton = document.getElementById("recordButton");
const recordText = document.getElementById("recordText");
const status = document.getElementById("status");
const transcript = document.getElementById("transcript");

recordButton.addEventListener("click", async () => {

    if (!isRecording) {
        await startRecording();
    } else {
        stopRecording();
    }

});

async function startRecording() {

    try {

        const stream = await navigator.mediaDevices.getUserMedia({
            audio: true
        });

        recorder = new MediaRecorder(stream);
        audioChunks = [];

        recorder.ondataavailable = event => {
            if (event.data.size > 0) {
                audioChunks.push(event.data);
            }
        };

        recorder.onstop = sendAudio;

        recorder.start();

        isRecording = true;

        recordButton.classList.add("recording");
        recordText.textContent = "Click to stop";
        status.textContent = "🎙 Listening...";

    } catch (error) {

        console.error(error);

        status.textContent =
            "❌ Microphone permission is required.";

    }
}

function stopRecording() {

    recorder.stop();

    recorder.stream.getTracks().forEach(track => track.stop());

    isRecording = false;

    recordButton.classList.remove("recording");

    recordText.textContent = "Processing...";
    status.textContent = "⏳ Converting speech to text...";

}

async function sendAudio() {

    const blob = new Blob(audioChunks, {
        type: "audio/webm"
    });

    const formData = new FormData();

    formData.append(
        "file",
        blob,
        "recording.webm"
    );

    formData.append(
        "language_code",
        "unknown"
    );

    try {

        const response = await fetch("/api/stt", {
            method: "POST",
            body: formData
        });

        const result = await response.json();

        if (result.error) {
            throw new Error(result.error);
        }

        transcript.textContent =
            result.transcript || "No speech detected.";

        status.textContent =
            "✅ Speech recognized";

        recordText.textContent =
            "Click to speak again";

    } catch (error) {

        console.error(error);

        status.textContent =
            "❌ STT failed";

        recordText.textContent =
            "Click to try again";
    }
}


// TTS

const speakButton =
    document.getElementById("speakButton");

const ttsText =
    document.getElementById("ttsText");

const language =
    document.getElementById("language");

const audioPlayer =
    document.getElementById("audioPlayer");

speakButton.addEventListener("click", async () => {

    const text = ttsText.value.trim();

    if (!text) {
        alert("Please enter some text.");
        return;
    }

    speakButton.disabled = true;
    speakButton.textContent = "Generating...";

    const formData = new FormData();

    formData.append("text", text);
    formData.append("language_code", language.value);
    formData.append("speaker", "shubh");

    try {

        const response = await fetch("/api/tts", {
            method: "POST",
            body: formData
        });

        const result = await response.json();

        if (result.error) {
            throw new Error(result.error);
        }

        const binary = atob(result.audio);

        const bytes = new Uint8Array(binary.length);

        for (let i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }

        const blob = new Blob(
            [bytes],
            { type: "audio/wav" }
        );

        audioPlayer.src =
            URL.createObjectURL(blob);

        audioPlayer.play();

    } catch (error) {

        console.error(error);

        alert("TTS failed. Check the backend.");

    } finally {

        speakButton.disabled = false;
        speakButton.textContent = "🔊 Speak";

    }

});