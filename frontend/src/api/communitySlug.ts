/**
 * TypeScript port of src/utils/community_tags.py:slugify and expected_filename.
 *
 * Must stay byte-for-byte equivalent. Regression check lives at
 * frontend/scripts/check-slug.mjs; the backend test suite also asserts
 * the canonical outputs the JS side relies on.
 */

export function slugify(name: string): string {
  if (typeof name !== 'string') return 'sponsor';
  const lowered = name.toLowerCase();
  const hyphenated = lowered.replace(/[^a-z0-9]+/g, '-');
  const stripped = hyphenated.replace(/^-+|-+$/g, '');
  return stripped || 'sponsor';
}

export function expectedFilename(sponsor: string, communityId: string | null | undefined): string | null {
  if (!communityId) return null;
  const short = communityId.split('-')[0];
  return `${slugify(sponsor)}-${short}.json`;
}
