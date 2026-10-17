/** Server-sent events, read from a fetch() body: the chat streams its progress
 * this way. EventSource can't be used, as it only does GET without a body. */

export type SseEvent = { event: string; data: unknown };

/** Feeds text chunks in as they arrive; calls `onEvent` for each complete event. */
export function createSseParser(onEvent: (event: SseEvent) => void) {
  let buffer = "";

  const flush = (block: string) => {
    let event = "message";
    const data: string[] = [];
    for (const raw of block.split("\n")) {
      const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
      if (!line || line.startsWith(":")) continue; // blank, or a keep-alive comment
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (!data.length) return;
    const text = data.join("\n");
    let parsed: unknown = text;
    try {
      parsed = JSON.parse(text);
    } catch {
      // not JSON: hand over the text as it is
    }
    onEvent({ event, data: parsed });
  };

  return {
    push(chunk: string) {
      buffer += chunk.replace(/\r\n/g, "\n");
      let end = buffer.indexOf("\n\n");
      while (end !== -1) {
        flush(buffer.slice(0, end));
        buffer = buffer.slice(end + 2);
        end = buffer.indexOf("\n\n");
      }
    },
    end() {
      if (buffer.trim()) flush(buffer);
      buffer = "";
    },
  };
}
