export function argValue(argv, name) {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] : undefined;
}

// An unknown flag is a mistake, never something to ignore.
export function checkFlags(argv, known) {
  const bad = argv.filter((x) => x.startsWith('--') && !known.includes(x));
  if (bad.length) throw new Error(`unknown option ${bad.join(' ')}; expected ${known.join(' ')}`);
}
