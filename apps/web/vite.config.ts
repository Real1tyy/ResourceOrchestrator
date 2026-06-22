/// <reference types="vitest/config" />
import babel from "@rolldown/plugin-babel";
import tailwindcss from "@tailwindcss/vite";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// plugin-react 6 transforms JSX with oxc (no babel pass), so React Compiler (target 19) is
// applied as a separate babel pass via @rolldown/plugin-babel. eslint-plugin-react-compiler
// enforces the rules-of-react the compiler relies on. Tailwind v4 via its first-party plugin.
export default defineConfig({
    plugins: [
        react(),
        babel({
            presets: [reactCompilerPreset({ target: "19" })],
            include: /\.[jt]sx?$/,
            exclude: /node_modules/,
        }),
        tailwindcss(),
    ],
    resolve: {
        alias: {
            "@": new URL("./src/", import.meta.url).pathname,
        },
    },
    server: { port: 5173, host: "127.0.0.1" },
    test: {
        environment: "jsdom",
        globals: false,
        setupFiles: ["./src/test-setup.ts"],
        // Unit tests live under src/; e2e/ is Playwright's.
        include: ["src/**/*.{test,spec}.{ts,tsx}"],
    },
});
