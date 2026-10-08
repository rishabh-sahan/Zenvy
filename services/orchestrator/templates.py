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
    # --- Added with slot locking. Kannada/Hindi are first drafts: they still need
    # --- a native-speaker review (see the note at the top of this file).
    "SLOT_TAKEN": {
        "en": "Sorry, {doctor} is not available at {time} on {date}. Free times that day: {alternatives}. Which one would you like?",
        "hi": "क्षमा करें, {doctor} {date} को {time} बजे उपलब्ध नहीं हैं। उस दिन खाली समय: {alternatives}। आप कौन सा चुनेंगे?",
        "kn": "ಕ್ಷಮಿಸಿ, {doctor} ಅವರು {date} ರಂದು {time} ಗಂಟೆಗೆ ಲಭ್ಯವಿಲ್ಲ. ಆ ದಿನ ಖಾಲಿ ಇರುವ ಸಮಯಗಳು: {alternatives}. ನೀವು ಯಾವುದನ್ನು ಆಯ್ಕೆ ಮಾಡುತ್ತೀರಿ?",
    },
    "SLOT_DAY_FULL": {
        "en": "Sorry, {doctor} has no free appointments on {date}. Which other date would suit you?",
        "hi": "क्षमा करें, {doctor} के पास {date} को कोई खाली अपॉइंटमेंट नहीं है। आपके लिए कौन सी दूसरी तारीख ठीक रहेगी?",
        "kn": "ಕ್ಷಮಿಸಿ, {doctor} ಅವರಿಗೆ {date} ರಂದು ಖಾಲಿ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್‌ಗಳಿಲ್ಲ. ನಿಮಗೆ ಬೇರೆ ಯಾವ ದಿನಾಂಕ ಸೂಕ್ತ?",
    },
    "DOCTOR_NOT_FOUND": {
        "en": "Sorry, I couldn't find a doctor matching that. Please tell me the doctor's name or the department you need.",
        "hi": "क्षमा करें, मुझे इस नाम का कोई डॉक्टर नहीं मिला। कृपया डॉक्टर का नाम या आवश्यक विभाग बताइए।",
        "kn": "ಕ್ಷಮಿಸಿ, ಆ ಹೆಸರಿನ ವೈದ್ಯರು ನನಗೆ ಸಿಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ವೈದ್ಯರ ಹೆಸರು ಅಥವಾ ನಿಮಗೆ ಬೇಕಾದ ವಿಭಾಗವನ್ನು ತಿಳಿಸಿ.",
    },
    "DOCTOR_AMBIGUOUS": {
        "en": "I found more than one match: {options}. Which doctor would you like? You can say the number.",
        "hi": "मुझे एक से अधिक डॉक्टर मिले: {options}। आप किस डॉक्टर से मिलना चाहेंगे? आप नंबर बता सकते हैं।",
        "kn": "ನನಗೆ ಒಂದಕ್ಕಿಂತ ಹೆಚ್ಚು ವೈದ್ಯರು ಸಿಕ್ಕಿದ್ದಾರೆ: {options}. ನೀವು ಯಾವ ವೈದ್ಯರನ್ನು ಭೇಟಿಯಾಗಲು ಬಯಸುತ್ತೀರಿ? ನೀವು ಸಂಖ್ಯೆಯನ್ನು ಹೇಳಬಹುದು.",
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