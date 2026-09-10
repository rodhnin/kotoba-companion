/**
 * Byte-level SSE plumbing for the events channel. EventSource had to go: behind the Next proxy a
 * backend restart leaves the old connection HALF-OPEN — readyState stays 1, onerror never fires, no
 * frame ever arrives again, and approval cards silently stop reaching the screen. The proxy hides the
 * EOF, so the only reliable liveness signal is the backend's own keepalive: a ": ping" comment after
 * every 15s of quiet, and SSE comments are invisible to the EventSource API — hence fetch() and a
 * parser here, where every byte counts as proof of life. SSE_STALE_MS tolerates two missed pings plus
 * jitter, because a false reconnect loop is worse than the half-open bug. SseParser implements the
 * WHATWG event-stream grammar: comment lines, multi-line data, CR/CRLF/LF, and split frames.
 */

export type SseFrame = { event: string; data: string };

export const SSE_PING_MS = 15000;
export const SSE_STALE_MS = 40000;

export function sseIsStale(lastAliveAt: number, now: number): boolean {
  return now - lastAliveAt > SSE_STALE_MS;
}

export class SseParser {
  private buf = "";
  private data: string[] = [];
  private event = "";

  feed(chunk: string): SseFrame[] {
    this.buf += chunk;
    const frames: SseFrame[] = [];
    for (;;) {
      const m = /\r\n|[\r\n]/.exec(this.buf);
      if (!m) break;
      if (m[0] === "\r" && m.index === this.buf.length - 1) break;
      const line = this.buf.slice(0, m.index);
      this.buf = this.buf.slice(m.index + m[0].length);
      const frame = this.parseLine(line);
      if (frame) frames.push(frame);
    }
    return frames;
  }

  private parseLine(line: string): SseFrame | null {
    if (line === "") {
      const frame = this.data.length ? { event: this.event || "message", data: this.data.join("\n") } : null;
      this.data = [];
      this.event = "";
      return frame;
    }
    if (line.startsWith(":")) return null;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") this.data.push(value);
    else if (field === "event") this.event = value;
    return null;
  }
}
