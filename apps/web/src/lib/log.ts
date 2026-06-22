// Frontend observability seam (skeleton). A structured client logger the UI calls for
// boot/error breadcrumbs. Today these go to the console as JSON; once the daemon exposes a
// log-ingest endpoint (MVP-2), `emit` forwards them so backend tracing and frontend events
// land in one timeline. This establishes structured client-side observability from day one.
type Level = "debug" | "info" | "warn" | "error";

type Fields = Record<string, unknown>;

function emit(level: Level, event: string, fields?: Fields): void {
    const record = { ts: new Date().toISOString(), level, event, ...fields };
    const line = JSON.stringify(record);
    if (level === "error") console.error(line);
    else if (level === "warn") console.warn(line);
    else console.log(line);
}

export const log = {
    debug: (event: string, fields?: Fields) => emit("debug", event, fields),
    info: (event: string, fields?: Fields) => emit("info", event, fields),
    warn: (event: string, fields?: Fields) => emit("warn", event, fields),
    error: (event: string, fields?: Fields) => emit("error", event, fields),
};

// Marks the UI as started — anchors the boot in the (future) unified log timeline.
export function logBoot(): void {
    log.info("ui.boot", { userAgent: navigator.userAgent });
}
