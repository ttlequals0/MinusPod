"""Cross-fetch differential fetcher (Layer 3).

Re-fetches an episode enclosure with a different podcast-client User-Agent
after transcription and diffs the two files. Audio that differs across
fetches is dynamically inserted by definition; identical audio is content
or a baked-in ad.

The primary download uses config.BROWSER_USER_AGENT (see
transcriber.download_audio); the refetch always presents a different,
realistic podcast-client string because ad decisioning keys on the request
fingerprint and UA + natural time spacing is the only variation available.
"""

import logging
import random

logger = logging.getLogger('podcast.differential')

# Realistic podcast-client UA strings for the refetch pool.
REFETCH_USER_AGENTS = (
    # Apple Podcasts on iOS
    'Podcasts/1650.1 CFNetwork/1494.0.7 Darwin/23.4.0',
    # Overcast
    'Overcast/3.0 (+http://overcast.fm/; iOS podcast app)',
    # Pocket Casts
    'PocketCasts/7.61 (+https://pocketcasts.com/)',
    # AntennaPod
    'AntennaPod/3.4.0',
    # Castro
    'Castro/2024.11 (iPhone; iOS 17.5)',
)

# DAI hosting / analytics-prefix domains. Prefix services chain the
# downstream hosts inside the URL path (e.g. pdst.fm/e/chrt.fm/track/...),
# so substring-matching one enclosure URL covers the whole redirect chain.
DAI_URL_DOMAINS = (
    'pdst.fm',
    'pscrb.fm',
    'mgln.ai',
    'megaphone.fm',
    'podtrac.com',
    'chrt.fm',
    'arttrk.com',
    'clrtpod.com',
    'dts.podtrac.com',
)


def pick_refetch_user_agent(first_ua: str | None) -> str:
    """Pick a refetch User-Agent from the pool, never equal to first_ua."""
    pool = [ua for ua in REFETCH_USER_AGENTS if ua != first_ua]
    return random.choice(pool)


def is_likely_dai_feed(enclosure_urls) -> bool:
    """True when any enclosure URL matches a known DAI/prefix domain."""
    for url in enclosure_urls or []:
        lowered = (url or '').lower()
        if any(domain in lowered for domain in DAI_URL_DOMAINS):
            return True
    return False
