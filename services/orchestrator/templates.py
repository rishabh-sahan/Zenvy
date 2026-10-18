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
    # --- Cancel / reschedule. Kannada/Hindi are first drafts and still need a native-speaker review.
    "CHANGE_NOT_LOGGED_IN": {
        "en": "Please log in first so I can find your appointments.",
        "hi": "कृपया पहले लॉग इन करें, ताकि मैं आपकी अपॉइंटमेंट देख सकूँ।",
        "kn": "ದಯವಿಟ್ಟು ಮೊದಲು ಲಾಗಿನ್ ಮಾಡಿ, ಆಗ ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್‌ಗಳನ್ನು ನಾನು ನೋಡಬಲ್ಲೆ.",
    },
    "CANCEL_NONE": {
        "en": "You have no upcoming appointments.",
        "hi": "आपकी कोई आगामी अपॉइंटमेंट नहीं है।",
        "kn": "ನಿಮಗೆ ಯಾವುದೇ ಮುಂಬರುವ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಇಲ್ಲ.",
    },
    "CANNOT_CHANGE": {
        "en": "Sorry, that appointment can't be changed any more: it has already started or has a consultation. Please contact the front desk.",
        "hi": "क्षमा करें, इस अपॉइंटमेंट में अब बदलाव नहीं हो सकता: यह शुरू हो चुकी है या इसका परामर्श हो चुका है। कृपया फ्रंट डेस्क से संपर्क करें।",
        "kn": "ಕ್ಷಮಿಸಿ, ಈ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಅನ್ನು ಇನ್ನು ಬದಲಾಯಿಸಲು ಸಾಧ್ಯವಿಲ್ಲ: ಅದು ಈಗಾಗಲೇ ಪ್ರಾರಂಭವಾಗಿದೆ ಅಥವಾ ಸಮಾಲೋಚನೆ ನಡೆದಿದೆ. ದಯವಿಟ್ಟು ಫ್ರಂಟ್ ಡೆಸ್ಕ್ ಅನ್ನು ಸಂಪರ್ಕಿಸಿ.",
    },
    "CHANGE_WHICH_ACTION": {
        "en": "Would you like to cancel your appointment or change its time?",
        "hi": "क्या आप अपनी अपॉइंटमेंट रद्द करना चाहते हैं या उसका समय बदलना चाहते हैं?",
        "kn": "ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಅನ್ನು ರದ್ದುಗೊಳಿಸಬೇಕೇ ಅಥವಾ ಸಮಯ ಬದಲಾಯಿಸಬೇಕೇ?",
    },
    "CANCEL_WHICH": {
        "en": "Which appointment would you like to cancel? {options}. You can say the number.",
        "hi": "आप कौन सी अपॉइंटमेंट रद्द करना चाहेंगे? {options}। आप नंबर बता सकते हैं।",
        "kn": "ನೀವು ಯಾವ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ರದ್ದುಗೊಳಿಸಲು ಬಯಸುತ್ತೀರಿ? {options}. ನೀವು ಸಂಖ್ಯೆಯನ್ನು ಹೇಳಬಹುದು.",
    },
    "RESCHED_WHICH": {
        "en": "Which appointment would you like to move? {options}. You can say the number.",
        "hi": "आप कौन सी अपॉइंटमेंट बदलना चाहेंगे? {options}। आप नंबर बता सकते हैं।",
        "kn": "ನೀವು ಯಾವ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬದಲಾಯಿಸಲು ಬಯಸುತ್ತೀರಿ? {options}. ನೀವು ಸಂಖ್ಯೆಯನ್ನು ಹೇಳಬಹುದು.",
    },
    "CANCEL_CONFIRM": {
        "en": "Do you want to cancel your appointment with {doctor} on {when}?",
        "hi": "क्या आप {doctor} के साथ {when} की अपनी अपॉइंटमेंट रद्द करना चाहते हैं?",
        "kn": "{doctor} ಜೊತೆ {when} ರಂದು ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಅನ್ನು ರದ್ದುಗೊಳಿಸಲೇ?",
    },
    "CANCEL_DONE": {
        "en": "Done. Your appointment with {doctor} on {when} is cancelled.",
        "hi": "हो गया। {doctor} के साथ {when} की आपकी अपॉइंटमेंट रद्द कर दी गई है।",
        "kn": "ಆಯಿತು. {doctor} ಜೊತೆ {when} ರಂದು ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ರದ್ದುಗೊಂಡಿದೆ.",
    },
    "CANCEL_KEPT": {
        "en": "Okay, I've kept your appointment with {doctor} on {when}.",
        "hi": "ठीक है, मैंने {doctor} के साथ {when} की आपकी अपॉइंटमेंट रखी है।",
        "kn": "ಸರಿ, {doctor} ಜೊತೆ {when} ರಂದು ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಹಾಗೆಯೇ ಇದೆ.",
    },
    "RESCHED_ASK_DATE": {
        "en": "Sure. Your appointment with {doctor} is on {when}. What new date would you like?",
        "hi": "ज़रूर। {doctor} के साथ आपकी अपॉइंटमेंट {when} को है। आप नई तारीख कौन सी चाहेंगे?",
        "kn": "ಸರಿ. {doctor} ಜೊತೆ ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ {when} ರಂದು ಇದೆ. ನಿಮಗೆ ಯಾವ ಹೊಸ ದಿನಾಂಕ ಬೇಕು?",
    },
    "RESCHED_CONFIRM": {
        "en": "Just to confirm: move your appointment with {doctor} from {old} to {date} at {time}. Shall I do this?",
        "hi": "पुष्टि के लिए: {doctor} के साथ अपनी अपॉइंटमेंट {old} से बदलकर {date} को {time} बजे करनी है। क्या मैं ऐसा करूँ?",
        "kn": "ದೃಢೀಕರಿಸಲು: {doctor} ಜೊತೆ ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಅನ್ನು {old} ರಿಂದ {date} ರಂದು {time} ಗಂಟೆಗೆ ಬದಲಾಯಿಸಬೇಕೇ?",
    },
    "RESCHED_DONE": {
        "en": "Done. Your appointment with {doctor} is now on {date} at {time}.",
        "hi": "हो गया। {doctor} के साथ आपकी अपॉइंटमेंट अब {date} को {time} बजे है।",
        "kn": "ಆಯಿತು. {doctor} ಜೊತೆ ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಈಗ {date} ರಂದು {time} ಗಂಟೆಗೆ ಇದೆ.",
    },
    "RESCHED_KEPT": {
        "en": "Okay, I've kept your appointment as it was.",
        "hi": "ठीक है, आपकी अपॉइंटमेंट पहले जैसी ही रखी है।",
        "kn": "ಸರಿ, ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಮೊದಲಿನಂತೆಯೇ ಇದೆ.",
    },
    "CHANGE_FAILED": {
        "en": "Sorry, I couldn't change the appointment because of a system issue. Please try again or contact the front desk.",
        "hi": "क्षमा करें, सिस्टम की समस्या के कारण अपॉइंटमेंट में बदलाव नहीं हो सका। कृपया फिर से कोशिश करें या फ्रंट डेस्क से संपर्क करें।",
        "kn": "ಕ್ಷಮಿಸಿ, ಸಿಸ್ಟಂ ಸಮಸ್ಯೆಯಿಂದಾಗಿ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬದಲಾಯಿಸಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ ಅಥವಾ ಫ್ರಂಟ್ ಡೆಸ್ಕ್ ಅನ್ನು ಸಂಪರ್ಕಿಸಿ.",
    },
    "MED_NOT_LOGGED_IN": {
        "en": "Please log in first so I can look up your medicines.",
        "hi": "कृपया पहले लॉग इन करें ताकि मैं आपकी दवाइयाँ देख सकूँ।",
        "kn": "ದಯವಿಟ್ಟು ಮೊದಲು ಲಾಗಿನ್ ಮಾಡಿ, ಆಗ ನಾನು ನಿಮ್ಮ ಔಷಧಗಳನ್ನು ನೋಡಬಹುದು.",
    },
    "MED_NONE": {
        "en": "I don't see any medicines prescribed by your doctor right now.",
        "hi": "अभी आपके डॉक्टर द्वारा लिखी कोई दवा मुझे नहीं दिख रही है।",
        "kn": "ಈಗ ನಿಮ್ಮ ವೈದ್ಯರು ಬರೆದುಕೊಟ್ಟ ಯಾವುದೇ ಔಷಧ ನನಗೆ ಕಾಣಿಸುತ್ತಿಲ್ಲ.",
    },
    "MED_LIST": {
        "en": "Your medicines: {medicines}. Please follow your doctor's instructions.",
        "hi": "आपकी दवाइयाँ: {medicines}। कृपया अपने डॉक्टर के निर्देशों का पालन करें।",
        "kn": "ನಿಮ್ಮ ಔಷಧಗಳು: {medicines}. ದಯವಿಟ್ಟು ನಿಮ್ಮ ವೈದ್ಯರ ಸೂಚನೆಗಳನ್ನು ಪಾಲಿಸಿ.",
    },
    "MED_NEXT": {
        "en": "Your next dose is {medicine}, {dose}, {when}.",
        "hi": "आपकी अगली खुराक {medicine}, {dose}, {when} है।",
        "kn": "ನಿಮ್ಮ ಮುಂದಿನ ಡೋಸ್ {medicine}, {dose}, {when}.",
    },
    "MED_NEXT_NONE": {
        "en": "You have no upcoming doses scheduled.",
        "hi": "आपकी कोई आने वाली खुराक निर्धारित नहीं है।",
        "kn": "ನಿಮಗೆ ಮುಂದಿನ ಯಾವುದೇ ಡೋಸ್ ನಿಗದಿಯಾಗಿಲ್ಲ.",
    },
    "MED_TAKEN": {
        "en": "Noted. I've marked {medicines} as taken.",
        "hi": "ठीक है। मैंने {medicines} को ली हुई के रूप में दर्ज कर लिया है।",
        "kn": "ಸರಿ. ನಾನು {medicines} ತೆಗೆದುಕೊಂಡಿದ್ದೀರಿ ಎಂದು ದಾಖಲಿಸಿದ್ದೇನೆ.",
    },
    "MED_TAKEN_NONE": {
        "en": "I don't see a dose due right now to mark as taken.",
        "hi": "अभी कोई खुराक नहीं दिख रही जिसे ली हुई दर्ज किया जा सके।",
        "kn": "ಈಗ ತೆಗೆದುಕೊಂಡಿದ್ದೀರಿ ಎಂದು ದಾಖಲಿಸಲು ಯಾವುದೇ ಡೋಸ್ ಕಾಣಿಸುತ್ತಿಲ್ಲ.",
    },
    "MED_FAILED": {
        "en": "Sorry, I couldn't check your medicines just now. Please try again in a moment.",
        "hi": "क्षमा करें, मैं अभी आपकी दवाइयाँ नहीं देख सका। कृपया थोड़ी देर बाद फिर कोशिश करें।",
        "kn": "ಕ್ಷಮಿಸಿ, ನಿಮ್ಮ ಔಷಧಗಳನ್ನು ಈಗ ನೋಡಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
    },
    # --- Symptoms and doctor suggestions, and "cancel" in the middle of a booking. Kannada/Hindi are first drafts
    # --- and still need a native-speaker review.
    "SYMPTOM_URGENT": {
        "en": "This may need urgent care. Please call 112 or go to the nearest emergency department now.",
        "hi": "इसमें तुरंत इलाज की ज़रूरत हो सकती है। कृपया अभी 112 पर कॉल करें या नज़दीकी इमरजेंसी विभाग में जाइए।",
        "kn": "ಇದಕ್ಕೆ ತುರ್ತು ಚಿಕಿತ್ಸೆ ಬೇಕಾಗಬಹುದು. ದಯವಿಟ್ಟು ಈಗಲೇ 112 ಗೆ ಕರೆ ಮಾಡಿ ಅಥವಾ ಹತ್ತಿರದ ತುರ್ತು ವಿಭಾಗಕ್ಕೆ ಹೋಗಿ.",
    },
    "DOCTOR_SUGGEST": {
        "en": "For this, I suggest seeing a {specialty}. Doctors available: {options}. Say the number or the name to book.",
        "hi": "इसके लिए मैं {specialty} से मिलने की सलाह देता हूँ। उपलब्ध डॉक्टर: {options}। बुक करने के लिए नंबर या नाम बताइए।",
        "kn": "ಇದಕ್ಕಾಗಿ {specialty} ಅವರನ್ನು ಭೇಟಿ ಮಾಡಲು ನಾನು ಸಲಹೆ ನೀಡುತ್ತೇನೆ. ಲಭ್ಯವಿರುವ ವೈದ್ಯರು: {options}. ಬುಕ್ ಮಾಡಲು ಸಂಖ್ಯೆ ಅಥವಾ ಹೆಸರು ಹೇಳಿ.",
    },
    "DOCTOR_SUGGEST_OTHER_CITY": {
        "en": "I couldn't find a {specialty} in your city, but these doctors are available in other cities: {options}. Say the number or the name to book.",
        "hi": "आपके शहर में मुझे {specialty} नहीं मिले, लेकिन दूसरे शहरों में ये डॉक्टर उपलब्ध हैं: {options}। बुक करने के लिए नंबर या नाम बताइए।",
        "kn": "ನಿಮ್ಮ ನಗರದಲ್ಲಿ {specialty} ಸಿಗಲಿಲ್ಲ, ಆದರೆ ಬೇರೆ ನಗರಗಳಲ್ಲಿ ಈ ವೈದ್ಯರು ಲಭ್ಯವಿದ್ದಾರೆ: {options}. ಬುಕ್ ಮಾಡಲು ಸಂಖ್ಯೆ ಅಥವಾ ಹೆಸರು ಹೇಳಿ.",
    },
    "DOCTOR_SUGGEST_AGAIN": {
        "en": "Which of these doctors would you like to book? {options}. You can say the number, or say no thanks.",
        "hi": "आप इनमें से किस डॉक्टर को बुक करना चाहेंगे? {options}। आप नंबर बता सकते हैं, या मना कर सकते हैं।",
        "kn": "ಇವರಲ್ಲಿ ಯಾವ ವೈದ್ಯರನ್ನು ಬುಕ್ ಮಾಡಲು ಬಯಸುತ್ತೀರಿ? {options}. ನೀವು ಸಂಖ್ಯೆ ಹೇಳಬಹುದು, ಅಥವಾ ಬೇಡ ಎನ್ನಬಹುದು.",
    },
    "SYMPTOM_ASK_PROBLEM": {
        "en": "Tell me what problem you are having, and I'll suggest the right kind of doctor.",
        "hi": "बताइए आपको क्या समस्या है, मैं सही प्रकार के डॉक्टर सुझाऊँगा।",
        "kn": "ನಿಮಗೆ ಯಾವ ತೊಂದರೆ ಇದೆ ಎಂದು ತಿಳಿಸಿ, ನಾನು ಸರಿಯಾದ ರೀತಿಯ ವೈದ್ಯರನ್ನು ಸೂಚಿಸುತ್ತೇನೆ.",
    },
    "SYMPTOM_NO_DOCTOR": {
        "en": "I'm sorry, I couldn't find a {specialty} right now. Please contact the front desk.",
        "hi": "क्षमा करें, अभी मुझे कोई {specialty} नहीं मिला। कृपया फ्रंट डेस्क से संपर्क करें।",
        "kn": "ಕ್ಷಮಿಸಿ, ಈಗ ನನಗೆ {specialty} ಸಿಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಫ್ರಂಟ್ ಡೆಸ್ಕ್ ಅನ್ನು ಸಂಪರ್ಕಿಸಿ.",
    },
    "SYMPTOM_TIP_SAFE": {
        "en": "Please rest, drink enough water and eat light food. If it gets severe or does not improve, please see a doctor.",
        "hi": "कृपया आराम करें, पर्याप्त पानी पिएँ और हल्का खाना खाएँ। अगर यह गंभीर हो जाए या ठीक न हो, तो कृपया डॉक्टर को दिखाइए।",
        "kn": "ದಯವಿಟ್ಟು ವಿಶ್ರಾಂತಿ ಪಡೆಯಿರಿ, ಸಾಕಷ್ಟು ನೀರು ಕುಡಿಯಿರಿ ಮತ್ತು ಹಗುರವಾದ ಆಹಾರ ಸೇವಿಸಿ. ಇದು ತೀವ್ರವಾದರೆ ಅಥವಾ ಸುಧಾರಿಸದಿದ್ದರೆ ದಯವಿಟ್ಟು ವೈದ್ಯರನ್ನು ಭೇಟಿ ಮಾಡಿ.",
    },
    "CANCEL_SCOPE": {
        "en": "Do you want to stop this booking, or cancel an appointment you already have? Say 1 for this booking, or 2 for the appointment you already have.",
        "hi": "क्या आप यह बुकिंग रोकना चाहते हैं, या अपनी पहले से बुक अपॉइंटमेंट रद्द करना चाहते हैं? इस बुकिंग के लिए 1 कहें, या पहले से बुक अपॉइंटमेंट के लिए 2 कहें।",
        "kn": "ನೀವು ಈ ಬುಕಿಂಗ್ ಅನ್ನು ನಿಲ್ಲಿಸಬೇಕೇ, ಅಥವಾ ನಿಮ್ಮಲ್ಲಿ ಈಗಾಗಲೇ ಇರುವ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಅನ್ನು ರದ್ದುಗೊಳಿಸಬೇಕೇ? ಈ ಬುಕಿಂಗ್‌ಗೆ 1 ಹೇಳಿ, ಈಗಾಗಲೇ ಇರುವ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್‌ಗೆ 2 ಹೇಳಿ.",
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


# Short pieces of wording used inside the doctor suggestions (kept here so they are translated in one place).
NEXT_FREE = {"en": "next free", "hi": "अगला खाली समय", "kn": "ಮುಂದಿನ ಖಾಲಿ ಸಮಯ"}
NO_FREE_SOON = {"en": "no free time in the next few days", "hi": "अगले कुछ दिनों में कोई खाली समय नहीं", "kn": "ಮುಂದಿನ ಕೆಲವು ದಿನಗಳಲ್ಲಿ ಖಾಲಿ ಸಮಯವಿಲ್ಲ"}
