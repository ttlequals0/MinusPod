"""Word-timed transcript of a bundled mid-roll: a known read's tail, then reads A, B and C."""


def _segment(*sentences):
    """One Whisper segment from (start, end, text) sentences, words spread evenly per sentence."""
    words = []
    for start, end, text in sentences:
        tokens = text.split()
        step = (end - start) / len(tokens)
        words += [{'word': token, 'start': round(start + i * step, 2),
                   'end': round(start + (i + 1) * step, 2)} for i, token in enumerate(tokens)]
        words[-1]['end'] = end
    return {'start': sentences[0][0], 'end': sentences[-1][1],
            'text': ' '.join(text for _, _, text in sentences), 'words': words}


BRANDS = ('Ledgerly', 'Acme Wash', 'Globex Foods', 'Headspace')

# Realistic Whisper segmentation: several 5-15 s segments per read.
SEGMENTS = [
    _segment((815.0, 829.4, 'Known Tool keeps your notes in sync across every device you own.'),
             (829.4, 831.4, 'Available on Plus and Pro plans.')),
    _segment((835.1, 840.0, 'Running a small team means juggling a lot of paperwork.'),
             (840.0, 848.0, 'Ledgerly keeps every invoice and receipt in one tidy place.')),
    _segment((848.0, 856.0, 'Ledgerly sends the reminders so you never chase a payment.'),
             (856.0, 860.0, 'Start free at ledgerly dot com.')),
    _segment((860.8, 866.0, 'Hey, this is Sam from The Other Show.'),
             (866.0, 869.4, 'Think for a moment about the small, relentless burdens.')),
    _segment((869.4, 872.0, 'They take up our headspace.'),
             (872.0, 884.0, 'Acme Wash is a premium laundry service that picks up and delivers.')),
    _segment((884.0, 896.0, 'Acme Wash folds everything and returns it the next day.')),
    _segment((896.0, 908.4, 'Try Acme Wash and get your first order half off.')),
    _segment((909.5, 918.0, 'Globex Foods brings fresh dinners to your door every week.')),
    _segment((918.0, 928.0, 'Globex Foods recipes take twenty minutes or less to make.')),
    _segment((928.0, 937.8, 'Order Globex Foods tonight and skip the grocery run.')),
    _segment((941.0, 960.0, 'Okay we are back with the rest of the show today.')),
]

# The known read's last sentence as its own segment, then a pause before read A.
SEGMENTS_OWN_TAIL = [
    _segment((815.0, 829.4, 'Known Tool keeps your notes in sync across every device you own.')),
    _segment((830.4, 832.3, 'Available on Plus and Pro plans.')),
    *SEGMENTS[1:],
]
