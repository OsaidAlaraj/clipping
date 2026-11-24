/** Shuffle the clips ahead of the current one; clips already seen and the current one keep their place.
 *  `rng` is injectable so the order can be tested. */
export function shuffleAhead<T>(list: T[], current: number, rng: () => number = Math.random): T[] {
  const head = list.slice(0, current + 1);
  const rest = list.slice(current + 1);
  for (let i = rest.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [rest[i], rest[j]] = [rest[j], rest[i]];
  }
  return [...head, ...rest];
}
