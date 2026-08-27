"""
Response templates for each orchestrator state, in Kannada, Hindi, and
English (roadmap Day 33).

IMPORTANT: these Kannada/Hindi translations were written directly and
have NOT been verified by a native speaker or checked via Mayura
back-translation, both of which the roadmap explicitly calls for
before treating templates as production-ready. Treat these as a
working draft, not final patient-facing copy.
"""

TEMPLATES = {
    "ASK_DOCTOR": {
        "en": "Sure, I can help you book an appointment. Which doctor or department would you like to see?",
        "hi": "ज़रूर, मैं आपकी अपॉइंटमेंट बुक करने में मदद कर सकता हूँ। आप किस डॉक्टर या विभाग से मिलना चाहेंगे?",
        "kn": "ಖಂಡಿತ, ನಾನು ನಿಮಗೆ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬುಕ್ ಮಾಡಲು ಸಹಾಯ ಮಾಡುತ್ತೇನೆ. ನೀವು ಯಾವ ವೈದ್ಯರನ್ನು ಅಥವಾ ವಿಭಾಗವನ್ನು ಭೇಟಿಯಾಗಲು ಬಯಸುತ್ತೀರಿ?",
    },
    "ASK_DATE": {
        "en": "Got it. What date would you like the appointment on?",
        "hi": "ठीक है। आप किस तारीख को अपॉइंटमेंट चाहेंगे?",
        "kn": "ಆಯಿತು. ನೀವು ಯಾವ ದಿನಾಂಕದಂದು ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬಯಸುತ್ತೀರಿ?",
    },
    "ASK_TIME": {
        "en": "And what time works best for you?",
        "hi": "और कौन सा समय आपके लिए सही रहेगा?",
        "kn": "ಮತ್ತು ಯಾವ ಸಮಯ ನಿಮಗೆ ಸೂಕ್ತವಾಗಿದೆ?",
    },
    "CONFIRM": {
        "en": "Just to confirm: an appointment with {doctor} on {date} at {time}. Shall I book this?",
        "hi": "पुष्टि के लिए: {doctor} के साथ {date} को {time} बजे अपॉइंटमेंट। क्या मैं इसे बुक करूँ?",
        "kn": "ದೃಢೀಕರಿಸಲು: {doctor} ಜೊತೆ {date} ರಂದು {time} ಗಂಟೆಗೆ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್. ನಾನು ಇದನ್ನು ಬುಕ್ ಮಾಡಲೇ?",
    },
    "CONFIRMED": {
        "en": "Your appointment with {doctor} on {date} at {time} is confirmed. See you then!",
        "hi": "{doctor} के साथ आपकी {date} को {time} बजे की अपॉइंटमेंट पक्की हो गई है। तब मिलते हैं!",
        "kn": "{doctor} ಜೊತೆ {date} ರಂದು {time} ಗಂಟೆಗೆ ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ದೃಢಪಡಿಸಲಾಗಿದೆ. ಆಗ ಭೇಟಿಯಾಗೋಣ!",
    },
    "BOOKING_FAILED": {
        "en": "Sorry, I couldn't complete the booking due to a system issue. Please try again or contact the front desk.",
        "hi": "क्षमा करें, सिस्टम की समस्या के कारण बुकिंग पूरी नहीं हो सकी। कृपया फिर से कोशिश करें या फ्रंट डेस्क से संपर्क करें।",
        "kn": "ಕ್ಷಮಿಸಿ, ಸಿಸ್ಟಂ ಸಮಸ್ಯೆಯಿಂದಾಗಿ ಬುಕಿಂಗ್ ಪೂರ್ಣಗೊಳಿಸಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ ಅಥವಾ ಫ್ರಂಟ್ ಡೆಸ್ಕ್ ಅನ್ನು ಸಂಪರ್ಕಿಸಿ.",
    },
    "CANCELLED": {
        "en": "No problem, I've cancelled that booking request. Let me know if you'd like to start over.",
        "hi": "कोई बात नहीं, मैंने वह बुकिंग रद्द कर दी है। यदि आप फिर से शुरू करना चाहें तो बताइए।",
        "kn": "ಪರವಾಗಿಲ್ಲ, ನಾನು ಆ ಬುಕಿಂಗ್ ವಿನಂತಿಯನ್ನು ರದ್ದುಗೊಳಿಸಿದ್ದೇನೆ. ನೀವು ಮತ್ತೆ ಪ್ರಾರಂಭಿಸಲು ಬಯಸಿದರೆ ತಿಳಿಸಿ.",
    },
}


def render_template(state: str, short_lang: str, **kwargs) -> str:
    """Render a state's template in the given language, filling any {placeholders}."""
    template = TEMPLATES[state].get(short_lang, TEMPLATES[state]["en"])
    return template.format(**kwargs)