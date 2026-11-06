// Swagger UI initialization. Lives as a served static file so the CSP
// script-src 'self' directive can permit it without the inline-script
// exception that a <script>...</script> block would require.
window.addEventListener('DOMContentLoaded', function () {
  var meta = document.querySelector('meta[name="minuspod-url-prefix"]');
  var prefix = meta ? meta.getAttribute('content') : '';
  window.SwaggerUIBundle({
    url: prefix + '/api/v1/openapi.yaml',
    dom_id: '#swagger-ui',
    presets: [
      window.SwaggerUIBundle.presets.apis,
      window.SwaggerUIBundle.SwaggerUIStandalonePreset,
    ],
    layout: 'BaseLayout',
  });
});
