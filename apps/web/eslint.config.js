import js from "@eslint/js";
import pluginQuery from "@tanstack/eslint-plugin-query";
import reactCompiler from "eslint-plugin-react-compiler";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
    { ignores: ["dist", "coverage", "playwright-report", "test-results"] },
    js.configs.recommended,
    ...tseslint.configs.recommended,
    ...pluginQuery.configs["flat/recommended"],
    {
        files: ["**/*.{ts,tsx}"],
        languageOptions: { globals: globals.browser },
        plugins: {
            "react-hooks": reactHooks,
            "react-compiler": reactCompiler,
        },
        rules: {
            "react-hooks/rules-of-hooks": "error",
            "react-hooks/exhaustive-deps": "warn",
            "react-compiler/react-compiler": "error",
        },
    },
);
