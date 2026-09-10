// What the user may attach. Text formats are read in the browser and pasted into the message, so any
// plain-text format works the same — the list is what we advertise, not a parser capability.
// One source: the picker's `accept`, the panel's kind check and the send path must all read from here —
// hand-kept copies drift apart, and a format pickable in one place but rejected in another looks broken.

export const TEXT_EXTS = ["txt", "md", "csv", "tsv", "json", "log", "yml", "yaml"] as const;
export const IMAGE_EXTS = ["png", "jpg", "jpeg", "gif", "webp", "bmp"] as const;

const TEXT_MIMES = ["text/plain", "text/markdown", "text/csv", "application/json"];

export function extensionOf(name: string): string {
  return (name.split(".").pop() || "").toLowerCase();
}

export function isTextAttachment(file: File): boolean {
  const ext = extensionOf(file.name);
  return (TEXT_EXTS as readonly string[]).includes(ext) || TEXT_MIMES.includes(file.type);
}

export function isImageAttachment(file: File): boolean {
  return file.type.startsWith("image/") || (IMAGE_EXTS as readonly string[]).includes(extensionOf(file.name));
}

export function isPdfAttachment(file: File): boolean {
  return file.type === "application/pdf" || extensionOf(file.name) === "pdf";
}

/** The file picker's `accept` string. */
export const ACCEPT_ATTR = [
  "image/*",
  ...TEXT_EXTS.map((e) => `.${e}`),
  ".pdf",
  ...TEXT_MIMES,
  "application/pdf",
].join(",");

/** Shown when a file is none of the above. */
export const REJECT_MESSAGE = `Only images, PDF, or text files (${TEXT_EXTS.map((e) => "." + e).join(", ")}).`;

/** What the attach control NAMES. Built from the same list as `accept` and the rejection above: a
 *  hand-written tooltip advertised .txt and .md only, so the one label on the control called a .csv
 *  the picker takes unsupported. */
export const ATTACH_HINT = `Attach an image, PDF, or text file (${TEXT_EXTS.map((e) => "." + e).join(", ")})`;
