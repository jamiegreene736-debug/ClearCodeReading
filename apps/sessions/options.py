BEHAVIORAL_OBSERVATION_OPTIONS = (
    ("task_persistence", "Task persistence"),
    ("attention_to_print", "Attention to print"),
    ("response_latency", "Response latency"),
    ("self_correction", "Self-correction"),
    ("requests_break", "Requests a break"),
    ("uses_strategy", "Uses the taught strategy"),
    ("confidence_to_attempt", "Confidence to attempt"),
)
BEHAVIORAL_RATING_OPTIONS = (
    ("rare", "Rare"),
    ("emerging", "Emerging"),
    ("inconsistent", "Inconsistent"),
    ("consistent", "Consistent"),
)
ERROR_PATTERN_OPTIONS = (
    ("phoneme_omission", "Phoneme omission"),
    ("phoneme_addition", "Phoneme addition"),
    ("phoneme_substitution", "Phoneme substitution"),
    ("phoneme_reversal", "Phoneme reversal"),
    ("short_vowel_confusion", "Short-vowel confusion"),
    ("long_vowel_confusion", "Long-vowel confusion"),
    ("consonant_confusion", "Consonant confusion"),
    ("blend_reduction", "Blend reduction"),
    ("digraph_confusion", "Digraph confusion"),
    ("syllable_division", "Syllable-division error"),
    ("high_frequency_word", "High-frequency-word error"),
    ("inflectional_ending", "Inflectional-ending error"),
    ("orthographic_rule", "Orthographic-rule error"),
    ("automaticity", "Automaticity"),
    ("transfer_to_text", "Transfer to connected text"),
)
BEHAVIORAL_CODES = {code for code, _ in BEHAVIORAL_OBSERVATION_OPTIONS}
BEHAVIORAL_RATINGS = {code for code, _ in BEHAVIORAL_RATING_OPTIONS}
ERROR_PATTERN_CODES = {code for code, _ in ERROR_PATTERN_OPTIONS}
ERROR_PATTERN_LABELS = dict(ERROR_PATTERN_OPTIONS)
