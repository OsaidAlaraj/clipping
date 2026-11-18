/** Turns a raw alert from the system into a plain sentence. The original text stays available as "details". */
const CHECKS: Record<string, string> = {
  caption_starts_with_disclosure: "the caption does not start with the required #ad label",
  duration: "the clip is shorter or longer than the allowed length",
};

export function plainAlert(message: string): { text: string; raw: string } {
  const raw = message;
  let m = message.match(/^(.*?): a clip failed compliance and was blocked \((\w+)\)\.?$/);
  if (m) return { text: `A clip for "${m[1]}" was blocked because ${CHECKS[m[2]] ?? "it failed a rule check"}. Open Review to fix or remake it.`, raw };
  m = message.match(/^(\w+) adapter \((\w+)\) failed (\d+) times/);
  if (m) return { text: `The ${m[2]} service did not answer after ${m[3]} tries. You can still add campaigns by hand.`, raw };
  m = message.match(/^A (\w+) job failed: .*?(azure_openai|deepgram|\w+) failed/);
  if (m) return { text: `The AI service could not finish the ${m[1]} step. Do it by hand for now, or try again later.`, raw };
  m = message.match(/^(.*?) was blocked: (.*?) \(hard rule \d+\)\.?$/);
  if (m) return { text: `"${m[1]}" is blocked: ${m[2].toLowerCase()}. It cannot be approved.`, raw };
  return { text: message, raw };
}
