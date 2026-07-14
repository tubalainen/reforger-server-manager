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
