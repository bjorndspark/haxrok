import js from "@eslint/js"
import globals from "globals"
import reactHooks from "eslint-plugin-react-hooks"
import reactRefresh from "eslint-plugin-react-refresh"
import tseslint from "typescript-eslint"

// Formatting is Prettier's job (`pnpm format`); these presets only carry
// correctness and consistency rules, so the two never fight.
export default tseslint.config(
  { ignores: ["dist"] },
  {
    extends: [
      js.configs.recommended,
      ...tseslint.configs.strictTypeChecked,
      ...tseslint.configs.stylisticTypeChecked,
    ],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      // `onClick={() => setX(v)}` and zustand's `(v) => set({ v })` are idiomatic.
      "@typescript-eslint/no-confusing-void-expression": ["error", { ignoreArrowShorthand: true }],
      // `.catch(() => {})` is the deliberate "best-effort, swallow" idiom.
      "@typescript-eslint/no-empty-function": ["error", { allow: ["arrowFunctions"] }],
      // Strict preset's options, plus numbers (ids in URL paths). Passing
      // options replaces the preset's, so the rest are restated.
      "@typescript-eslint/restrict-template-expressions": [
        "error",
        {
          allowNumber: true,
          allowAny: false,
          allowBoolean: false,
          allowNullish: false,
          allowRegExp: false,
          allowNever: false,
        },
      ],
      // TS narrows a flag after `if (cancelled) return` and never widens it
      // across an await or callback (microsoft/TypeScript#9998), so the
      // voice code's post-await cancellation re-checks read as "always
      // false". Those re-checks are the point; the rule can't model them.
      "@typescript-eslint/no-unnecessary-condition": "off",
    },
  },
  {
    // Fakes stub browser APIs with no-op methods.
    files: ["**/*.test.{ts,tsx}"],
    rules: {
      "@typescript-eslint/no-empty-function": "off",
    },
  },
)
