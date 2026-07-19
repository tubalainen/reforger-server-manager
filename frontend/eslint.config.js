import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import globals from 'globals'

// Deliberately lean: this is here to catch what human review keeps missing —
// unused variables, undefined names, broken template refs — not to argue about
// formatting. Vue's "essential" ruleset only flags real errors.
export default [
  { ignores: ['dist/**', 'node_modules/**'] },
  js.configs.recommended,
  ...pluginVue.configs['flat/essential'],
  {
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
      globals: { ...globals.browser },
    },
    rules: {
      'no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      // Catch a template that calls a handler the script no longer defines — the
      // exact regression in #111, where a refactor dropped addModByRow but left
      // @click="addModByRow(row)" behind, so "Add" silently did nothing. Not part
      // of Vue's "essential" set, so it has to be turned on explicitly.
      'vue/no-undef-properties': 'error',
      // Route views are named after their route (Instances, Templates, Login...).
      // That is the convention here, not an accident.
      'vue/multi-word-component-names': 'off',
    },
  },
  {
    files: ['src/__tests__/**/*.js'],
    languageOptions: { globals: { ...globals.node } },
  },
]
