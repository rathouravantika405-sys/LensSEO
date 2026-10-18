# LensSEO — shareable interview demo

This source is ready to deploy on Render's free Python web-service tier.

## Deploy from GitHub
1. Upload the contents of this folder into a GitHub repository.
2. Create a Render web service using that repository, Python runtime, and free plan.
3. Build: `pip install -r requirements.txt`
4. Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Set environment values: `LENSSEO_PUBLIC_MODE=true`, `LENSSEO_ALLOWED_DOMAINS=pw.live,pwgulf.com,pwskills.com,example.org`.
6. Once the service is live, check `/api/health` and click **Try sample report**; then test an authorized URL.

The source includes `render.yaml` if using Render Blueprint. This is an interview demo with bounded crawling; it is **not** a production-scale crawler. Public-demo mode caps audits, restricts targets to known demonstration domains, and disables paid AI calls. The report has the five specified columns.

Keep credentials and private API keys out of the repository. The app needs no paid API key for technical audits.
