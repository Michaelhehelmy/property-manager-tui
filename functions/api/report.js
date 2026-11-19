/**
 * Cloudflare Pages Function: receive user report JSON and create a GitHub issue.
 * Configure secrets in Cloudflare Pages dashboard (Settings → Environment variables / Secrets):
 *   REPORT_GITHUB_TOKEN  (required) - GitHub PAT with issues:write on the repo
 *   REPORT_GITHUB_REPO   (optional) - e.g. "Michaelhehelmy/property-manager-tui"
 *   REPORT_GITHUB_LABELS (optional) - comma-separated, default "user-report"
 *   REPORT_MAX_BYTES     (optional) - default 500000
 */
export async function onRequestPost(context) {
  const { request, env } = context;
  const maxBytes = parseInt(env.REPORT_MAX_BYTES || '500000', 10);
  try {
    const contentType = (request.headers.get('content-type') || '').toLowerCase();
    if (!contentType.includes('application/json')) {
      return Response.json({ ok: false, error: 'Content-Type must be application/json.' }, { status: 415 });
    }
    const raw = await request.text();
    if (!raw || raw.length > maxBytes) {
      return Response.json({ ok: false, error: `Report too large or empty (max ${maxBytes} bytes).` }, { status: 413 });
    }
    let data;
    try {
      data = JSON.parse(raw);
    } catch (e) {
      return Response.json({ ok: false, error: 'Invalid JSON.' }, { status: 400 });
    }
    // Basic validation: must look like our report
    if (typeof data !== 'object' || data === null) {
      return Response.json({ ok: false, error: 'Invalid report format.' }, { status: 400 });
    }
    if (!data.report_kind && !data.diagnostics && !data.client_device && !data.user_report) {
      return Response.json({ ok: false, error: 'Report does not look like a SPMS issue report (missing expected fields).' }, { status: 400 });
    }

    const token = env.REPORT_GITHUB_TOKEN || env.GITHUB_TOKEN;
    if (!token) {
      return Response.json({ ok: false, error: 'Report uploads are not configured on this server. Please download the report file and open an issue on GitHub manually.' }, { status: 503 });
    }
    const repo = (env.REPORT_GITHUB_REPO || 'Michaelhehelmy/property-manager-tui').trim();
    const labelsRaw = env.REPORT_GITHUB_LABELS || 'user-report,from-landing';
    const labels = labelsRaw.split(',').map(s => s.trim()).filter(Boolean).slice(0, 10);

    const user = data.user_report || {};
    const titleBase = (user.title || '').trim() || 'User-submitted issue report (from landing page)';
    const title = titleBase.slice(0, 120);

    const generatedAt = data.generated_at || data.generated_at_client || new Date().toISOString();
    const summary = [
      '## User-submitted report (from landing page)',
      '',
      `- Submitted via landing page upload at: ${new Date().toISOString()}`,
      `- Client generated_at: ${data.generated_at_client || 'n/a'}`,
      `- Server/app diagnostics generated_at: ${data.generated_at || 'n/a'}`,
      '',
      '### User details',
      `- Category: ${user.category || 'n/a'}`,
      `- Severity: ${user.severity || 'n/a'}`,
      '',
      '### Steps to reproduce',
      user.steps_to_reproduce || '(not provided)',
      '',
      '### Expected behavior',
      user.expected_behavior || '(not provided)',
      '',
      '### Actual behavior / error details',
      user.actual_behavior || '(not provided)',
      '',
      '### Additional notes',
      user.notes || '(not provided)',
      '',
      '### Attachments note',
      'The full JSON report is attached below in the issue body (reviewed by the uploader before sending).',
      '',
      '<details><summary>Full report JSON (sanitized client/server bundle)</summary>',
      '',
      '```json',
      JSON.stringify(data, null, 2).slice(0, 60000),
      '',
      '```',
      '',
      '</details>',
      '',
      '> Created via landing page upload. Please review before acting on any information.',
    ].join('\n');

    const body = summary.slice(0, 60000);

    const ghRes = await fetch(`https://api.github.com/repos/${repo}/issues`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'spms-landing-report',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ title, body, labels }),
    });

    if (!ghRes.ok) {
      const txt = await ghRes.text().catch(() => '');
      return Response.json({ ok: false, error: 'Failed to create GitHub issue (server misconfigured or API error).', status: ghRes.status }, { status: 502 });
    }
    const issue = await ghRes.json();
    return Response.json({ ok: true, issue_url: issue.html_url, issue_number: issue.number }, { status: 200 });
  } catch (e) {
    return Response.json({ ok: false, error: 'Unexpected server error.' }, { status: 500 });
  }
}

export async function onRequest(context) {
  return Response.json({ ok: false, error: 'Method not allowed. Use POST.' }, { status: 405 });
}
