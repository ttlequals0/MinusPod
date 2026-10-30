"""YouTube episode field rules shared by discovery, the backfill and the database."""
import re

MIDNIGHT_ONLY = re.compile(r'^\d{4}-\d{2}-\d{2}(T00:00:00(Z|\+00:00)?)?$')
PLACEHOLDER_TITLES = {'[Private video]': 'Private episode', '[Deleted video]': 'Deleted episode',
                      '[Unavailable video]': 'Unavailable episode'}
