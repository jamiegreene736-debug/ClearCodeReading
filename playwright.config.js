const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests/browser',
  outputDir: './test-results/responsive',
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.RESPONSIVE_BASE_URL || 'http://127.0.0.1:8000',
    channel: 'chrome',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
