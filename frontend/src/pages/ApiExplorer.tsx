import { useEffect } from 'react';
import { pageHeader, pageTitle, pageDescription } from '../components/pageStyles';

export default function ApiExplorer() {
  useEffect(() => {
    let cancelled = false;
    const container = document.getElementById('swagger-ui');
    void (async () => {
      const [{ default: SwaggerUIBundle }] = await Promise.all([
        import('swagger-ui-dist/swagger-ui-bundle.js'),
        import('swagger-ui-dist/swagger-ui.css'),
      ]);
      if (cancelled) return;
      SwaggerUIBundle({
        url: '/demo/openapi.yaml',
        dom_id: '#swagger-ui',
        presets: [SwaggerUIBundle.presets.apis],
        layout: 'BaseLayout',
        tryItOutEnabled: true,
      });
    })();
    return () => {
      cancelled = true;
      if (container) container.innerHTML = '';
    };
  }, []);

  return (
    <div className="max-w-5xl mx-auto pb-20">
      <div className={pageHeader}>
        <div>
          <h1 className={pageTitle}>API explorer</h1>
          <p className={pageDescription}>GET requests return the demo's recorded data. Anything else is disabled.</p>
        </div>
      </div>
      <div id="swagger-ui" />
    </div>
  );
}
