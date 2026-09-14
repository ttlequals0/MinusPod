export function stripHtml(
  input: string | null | undefined,
  { collapse = false }: { collapse?: boolean } = {}
): string {
  if (!input) return '';
  let text: string;
  if (typeof DOMParser !== 'undefined') {
    const doc = new DOMParser().parseFromString(input, 'text/html');
    text = doc.body.textContent || '';
  } else {
    let prev: string;
    let curr = input;
    do {
      prev = curr;
      curr = curr.replace(/<[^>]*>/g, '');
    } while (curr !== prev);
    text = curr;
  }
  return collapse ? text.replace(/\s+/g, ' ').trim() : text;
}
