export function stripHtml(input: string | null | undefined): string {
  if (!input) return '';
  if (typeof DOMParser !== 'undefined') {
    const doc = new DOMParser().parseFromString(input, 'text/html');
    return doc.body.textContent || '';
  }
  let prev: string;
  let curr = input;
  do {
    prev = curr;
    curr = curr.replace(/<[^>]*>/g, '');
  } while (curr !== prev);
  return curr;
}
