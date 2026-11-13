/**
 * Copy text to the clipboard; returns true on success so callers show "Copied" only when it happened.
 * Falls back to a hidden input + execCommand when navigator.clipboard is unavailable (common over plain
 * http on a LAN, since the clipboard API needs a secure context).
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const input = document.createElement('input');
      input.value = text;
      document.body.appendChild(input);
      input.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(input);
      return ok;
    } catch {
      return false;
    }
  }
}
