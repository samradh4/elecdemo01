from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from v430_features import admin_ops_portal_v430

router = APIRouter(prefix="/v4", tags=["v4.4.1-admin-download-fix"])


@router.get("/admin-ops", response_class=HTMLResponse, include_in_schema=False)
def admin_ops_portal_v441():
    """Serve the v4.4 admin portal with a safe Excel download button.

    The previous table renderer embedded JSON.stringify(filename) inside a
    double-quoted onclick attribute. The filename's JSON quotes therefore
    terminated the HTML attribute early, leaving an inert Excel button.
    """
    html = admin_ops_portal_v430()
    if not isinstance(html, str):
        html = str(html)

    broken = "onclick=\"downloadPdfResult('${j.jobId}',${JSON.stringify(j.filename)})\""
    fixed = "onclick=\"downloadPdfResult('${j.jobId}')\""
    html = html.replace(broken, fixed)

    # Override the old two-argument function after the existing scripts.  The
    # server already sets Content-Disposition, while the browser-side fallback
    # gives each downloaded workbook a stable .xlsx name.
    patch_script = r'''
<script>
window.downloadPdfResult = async function(id){
  try{
    await downloadAuth(`/admin/pdf/jobs/${id}/xlsx`, `electoral-roll-${id}.xlsx`);
  }catch(e){
    alert(e && e.message ? e.message : 'Excel download failed');
  }
};
</script>
'''
    html = html.replace("Constituency Manager 4.3", "Constituency Manager 4.4.1")
    if "window.downloadPdfResult = async function(id)" not in html:
        html = html.replace("</body></html>", patch_script + "</body></html>")
    return html
