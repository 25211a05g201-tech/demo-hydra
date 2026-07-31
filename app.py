"""
HYDRA Case & Document Manager
==============================
Single-file Streamlit application implementing:
  1. Whitelist-based authentication (Google OAuth placeholder + working demo login)
  2. Google Sheets-backed database (User_Whitelist, HYDRA_Cases, Audit_Logs,
     Unassigned_Scans)
  3. Automatic Google Drive folder scaffolding per case (legacy service-account
     path, still used for Pending Review file moves) PLUS a credential-free
     Apps Script "uploadFile" path used for all direct file uploads (Bulk
     Upload, New Case complaint document, and Approved Notice PDFs)
  4. Audit logging on login, case creation, document upload, AI briefings,
     notice sign-off, and case closure
  5. Head (Super-Admin) Performance & Impact Analytics Board with KPI metrics,
     breakdown charts, an interactive filterable case grid, officer assignment,
     and validated case closure with audit logging.
  6. Document Checklist Grid (Status Matrix) — a 5-row per-case matrix of the
     mandatory HYDRA documents. Each document's status column now stores
     EITHER "Pending" OR the actual Google Drive file URL returned by the
     Apps Script uploader. The grid renders a green "✅ View File" link
     straight to that URL whenever the status value starts with "http".
  7. Bulk Upload Auto-Sorter — operators can drag-and-drop many files at once;
     the backend parses filenames to detect the Case ID and document type,
     uploads matched files straight into the right case folder via the Apps
     Script "uploadFile" action, and routes anything unmatched/unreadable/
     blurry into an "Unassigned_Scans" Drive folder (legacy service-account
     path) for manual triage.
  8. Pending Review Queue — an exception-handling tab (Operator + Head) that
     lists everything sitting in "Unassigned_Scans" and lets a user manually
     map a scan to a Case ID + Document Type, which moves the file in Drive
     and updates the case's checklist grid automatically.
  9. Dual AI Briefing (Gemini 1.5 Flash) —
       Briefing 1: on case creation, Gemini reads the raw complaint text
       and/or an attached complaint document and writes a structured
       summary into "complaint_brief".
       Briefing 2: when a "Field Inspection Report" document is filed
       (via Bulk Upload or Pending Review), Gemini reads it — including
       handwritten scans — directly from the in-memory bytes that were
       just uploaded (no re-download round trip needed for the Bulk
       Upload path) and writes a "Field Findings Brief" into
       "field_report_brief", then advances the case status to "Inspected".
     Both briefings — and the AI Notice Generator below — are fully
     crash-proofed: if Gemini is not configured, has a bad/missing API
     key, or throws any error at call time, the app never stops execution.
     It shows a mild st.warning() and falls back to a manual/default
     summary so the case (or field report / notice draft) still saves
     successfully.
 10. AI Notice Generator with QR Code — for any "Inspected" case, the Head
     can have Gemini draft a formal HYDRA show-cause notice, edit it, and
     (Head-only) approve & sign off. Approval renders a PDF with an
     embedded case-tracking QR code and files it via the Apps Script
     "uploadFile" action into the case's "Approved_Notices/" Drive folder,
     then advances status to "Notice Served". Drafting itself is
     crash-proofed the same way as the two briefings above.
 11. Standalone local utility (no Streamlit/Sheets/Drive dependency) that
     splits one giant combined scanned PDF into separate documents by
     detecting blank/near-blank separator pages.

--------------------------------------------------------------------------------
SETUP INSTRUCTIONS
--------------------------------------------------------------------------------
1. Requirements (requirements.txt):
     streamlit
     requests
     google-auth
     google-api-python-client
     pandas
     Pillow                  # optional — enables the image blur heuristic
     PyPDF2                  # optional — enables deeper PDF-corruption checks
     pypdf                   # used by the standalone PDF splitter utility
     google-generativeai     # optional — enables the Dual AI Briefing + Notice Drafting
     qrcode                  # optional — enables QR codes on generated notices
     reportlab                # optional — enables notice PDF rendering

   Pillow, PyPDF2, google-generativeai, qrcode, and reportlab are all optional.
   If any is missing, the corresponding feature degrades gracefully (AI
   briefings show a placeholder message, notice generation is disabled with
   a clear error) and the rest of the app keeps working.

   NOTE: `gspread` has been removed — the database layer below no longer
   talks to the Sheets API directly. `google-auth` / `google-api-python-client`
   are still required only if you want the LEGACY Google Drive service-account
   path (folder scaffolding for Pending Review moves, etc — see step 3
   below). Direct file uploads (Bulk Upload, New Case complaint doc, and
   Approved Notice PDFs) now go through the credential-free Apps Script
   "uploadFile" action instead and do NOT require the service account.

2. CREDENTIAL-FREE DATABASE + FILE UPLOADS (Google Sheets + Drive, via CSV
   export + a single Apps Script Web App) ---

   Instead of a service account + gspread, the "database" (User_Whitelist,
   HYDRA_Cases, Audit_Logs, Unassigned_Scans) is a single Google Sheet with
   four tabs, read via the public CSV export endpoint and written to via a
   small Google Apps Script Web App you deploy from that same Sheet. That
   same Web App also now accepts an "uploadFile" action that writes a
   base64-encoded file straight into a per-case Drive subfolder and hands
   back a shareable "file_url". No Google Cloud Console project or service
   account is needed for either of these.

   a) Create one Google Sheet with four tabs, named EXACTLY:
        User_Whitelist, HYDRA_Cases, Audit_Logs, Unassigned_Scans
      Give each tab a header row matching (in any order for reads — but see
      the IMPORTANT note below about the write path) the constants below in
      this file:
        User_Whitelist  -> WHITELIST_HEADERS
        HYDRA_Cases     -> CASES_HEADERS
        Audit_Logs      -> AUDIT_HEADERS
        Unassigned_Scans -> UNASSIGNED_HEADERS
      Add at least one row to User_Whitelist so you can log in, e.g.:
        gmail_id             | role  | department | name
        head@example.com     | Head  | HYDRA HQ   | Jane Doe

      IMPORTANT — column order now matters for writes: new rows are sent to
      the Apps Script Web App as a flat, position-based array (see "b)"
      below), built client-side by walking CASES_HEADERS / AUDIT_HEADERS /
      UNASSIGNED_HEADERS in order. That means each tab's header row in the
      actual Google Sheet MUST be in that exact same left-to-right order,
      or values will land in the wrong columns. Reads (`read_sheet`, via
      pandas) are unaffected by column order since they match by header
      name.

      ALSO IMPORTANT — the document-status columns (layout_a_status,
      field_report_status, layout_b1_revenue_status, layout_b2_ghmc_status,
      layout_b3_water_status) now store either the literal string "Pending"
      OR the full https:// Drive file URL returned by "uploadFile" — NOT
      the word "Uploaded". The checklist grid (see
      render_document_checklist_grid()) treats any value starting with
      "http" as "this document is uploaded, and here's the link".

   b) Share the Sheet as "Anyone with the link — Viewer" (required so
      pd.read_csv can fetch the gviz CSV export with no auth).

   c) In the same Sheet, open Extensions -> Apps Script and paste a Web
      App that accepts POST requests shaped like:
        {"action": "writeRow", "sheetName": "<tab>", "rowData": [val1, val2, ...]}
        {"action": "update",   "sheetName": "<tab>", "matchColumn": "<col>",
         "matchValue": "<val>", "rowData": {col: val, ...}}
        {"action": "uploadFile", "caseId": "<case id>", "fileName": "<name>",
         "fileBytes": "<base64 string>", "mimeType": "<mime>",
         "subFolder": "Layouts_and_Field_Reports" | "Approved_Notices"}
      "writeRow" sends `rowData` as a flat, position-based ARRAY (already
      ordered to match the target tab's header row exactly by the Python
      code below — see dict_to_ordered_row() / write_sheet()), so the Apps
      Script side can append it directly with no header lookup needed.
      "update" still sends `rowData` as an OBJECT (column_name -> value),
      since updates are partial and only touch specific columns; the Apps
      Script side finds the first row where matchColumn == matchValue and
      overwrites only the columns present in rowData, leaving every other
      column untouched. "uploadFile" decodes the base64 `fileBytes`,
      creates (or reuses) the case's Drive subfolder, writes the file
      there, makes it link-shareable, and returns
      {"ok": true, "file_url": "https://drive.google.com/..."}.

      A minimal example (extend your existing doPost with the uploadFile
      branch):

        function doPost(e) {
          var body = JSON.parse(e.postData.contents);
          var ss = SpreadsheetApp.getActiveSpreadsheet();

          if (body.action === "uploadFile") {
            var rootFolder = getOrCreateFolder_(DriveApp.getRootFolder(), "HYDRA_Cases");
            var caseFolder = getOrCreateFolder_(rootFolder, body.caseId);
            var subFolder = getOrCreateFolder_(caseFolder, body.subFolder);
            var blob = Utilities.newBlob(
              Utilities.base64Decode(body.fileBytes), body.mimeType, body.fileName
            );
            var file = subFolder.createFile(blob);
            file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
            return ContentService.createTextOutput(
              JSON.stringify({ ok: true, file_url: file.getUrl() })
            ).setMimeType(ContentService.MimeType.JSON);
          }

          var sheet = ss.getSheetByName(body.sheetName);
          var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];

          if (body.action === "writeRow") {
            // rowData already arrives as a flat array, in the exact same
            // left-to-right order as this tab's header row — append as-is.
            sheet.appendRow(body.rowData);
          } else if (body.action === "update") {
            var data = sheet.getDataRange().getValues();
            var matchColIdx = headers.indexOf(body.matchColumn);
            for (var r = 1; r < data.length; r++) {
              if (String(data[r][matchColIdx]) === String(body.matchValue)) {
                Object.keys(body.rowData).forEach(function(col) {
                  var colIdx = headers.indexOf(col);
                  if (colIdx !== -1) sheet.getRange(r + 1, colIdx + 1).setValue(body.rowData[col]);
                });
                break;
              }
            }
          }
          return ContentService.createTextOutput(JSON.stringify({ok: true}))
            .setMimeType(ContentService.MimeType.JSON);
        }

        function getOrCreateFolder_(parent, name) {
          var it = parent.getFoldersByName(name);
          if (it.hasNext()) return it.next();
          return parent.createFolder(name);
        }

      Deploy it (Deploy -> New deployment -> Web app), execute as yourself,
      access "Anyone", and copy the resulting Web App URL.

   d) Add both URLs to .streamlit/secrets.toml:

        DATABASE_URL = "https://docs.google.com/spreadsheets/d/<YOUR_SHEET_ID>"
        WRITE_API_URL = "https://script.google.com/macros/s/<YOUR_DEPLOYMENT_ID>/exec"

3. GOOGLE DRIVE SERVICE ACCOUNT (OPTIONAL / LEGACY — only needed for the
   Pending Review Queue's "move file between folders" step, and for
   creating the case folder scaffold up front) still uses a Google Cloud
   service account with the Drive API enabled, since that specific
   move-a-file-between-folders operation isn't exposed by the Apps Script
   uploadFile action. This is NOT required to run the app, and is no
   longer required for the Bulk Upload Auto-Sorter's matched-file path,
   the New Case complaint document, or Approved Notice PDFs — all of those
   now go through the credential-free Apps Script "uploadFile" action
   described in step 2 above. Without this service account configured,
   case creation, the analytics board, "My Cases", Bulk Upload of matched
   documents, and Notice approval all work normally — only the Pending
   Review Queue (which still needs to *move* an already-uploaded
   Unassigned_Scans file between Drive folders) shows a "Drive isn't
   connected yet" notice and disables itself until you add this block.

   To enable it: create the service account, enable the Drive API, share
   the target Drive location with its email, and add its JSON key to
   secrets.toml:

     [gcp_service_account]
     type = "service_account"
     project_id = "..."
     private_key_id = "..."
     private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
     client_email = "...@....iam.gserviceaccount.com"
     client_id = "..."
     token_uri = "https://oauth2.googleapis.com/token"

     # Optional: only needed once real Google OAuth Sign-In is wired up.
     [google_oauth]
     client_id = "your-oauth-client-id.apps.googleusercontent.com"
     client_secret = "your-oauth-client-secret"
     redirect_uri = "http://localhost:8501"

     # Optional: enables the Dual AI Briefing and AI Notice Generator.
     # Get a key from Google AI Studio (https://aistudio.google.com/app/apikey).
     # NOTE: this block is genuinely optional now — if it's missing, wrong,
     # expired, or Gemini errors out at call time, every AI feature falls
     # back to a manual/default summary instead of blocking the app. See
     # generate_complaint_brief(), generate_field_report_brief(), and
     # generate_notice_draft() below.
     [gemini]
     api_key = "your-google-ai-studio-api-key"

4. Run:  streamlit run app.py

5. Standalone PDF splitter (no Streamlit needed):
     python app.py --split-pdf /path/to/giant_scan.pdf /path/to/output_dir
--------------------------------------------------------------------------------
"""

import base64
import io
import os
import re
import uuid
from datetime import datetime, date

import streamlit as st
import pandas as pd
import requests
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ---- Optional AI / PDF-generation dependencies ---------------------------------
# Each of these degrades gracefully if the package isn't installed, mirroring
# the existing Pillow/PyPDF2-optional pattern used elsewhere in this file.
try:
    import google.generativeai as genai

    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

try:
    import qrcode

    QRCODE_AVAILABLE = True
except ImportError:
    QRCODE_AVAILABLE = False

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib import colors as rl_colors

    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

try:
    from pypdf import PdfReader as PypdfReader, PdfWriter as PypdfWriter

    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False


# --------------------------------------------------------------------------------
# CONSTANTS
# --------------------------------------------------------------------------------

APP_TITLE = "HYDRA Case & Document Manager"

SCOPES = [
    "https://www.googleapis.com/auth/drive",
]

WHITELIST_SHEET_NAME = "User_Whitelist"
CASES_SHEET_NAME = "HYDRA_Cases"
AUDIT_SHEET_NAME = "Audit_Logs"
UNASSIGNED_SHEET_NAME = "Unassigned_Scans"

WHITELIST_HEADERS = ["gmail_id", "role", "department", "name"]

# NOTE: the original columns are untouched (anti-forget rule). The
# document-checklist-grid feature adds link/uploader/department columns for
# each of the five mandatory documents, and the AI Notice Generator feature
# adds notice-related columns, all appended at the end of the header list.
# The four "extended enforcement tracking" columns (joint_survey_status,
# objection_status, stay_order_status, original_permitting_officer) are new
# and are inserted right after the five document-status columns / before
# land_saved_value, per the exact column order the underlying Google Sheet
# tab now uses. Because writes are now position-based (see write_sheet()
# below), THIS LIST'S ORDER MUST MATCH THE ACTUAL SHEET'S HEADER ROW
# EXACTLY, left to right.
#
# NOTE ON *_status COLUMNS: layout_a_status / field_report_status /
# layout_b1_revenue_status / layout_b2_ghmc_status / layout_b3_water_status
# now hold either "Pending" (default) or the full https:// Drive file URL
# returned by the Apps Script "uploadFile" action — see
# render_document_checklist_grid() below, which renders a green
# "✅ View File" link whenever the value starts with "http".
CASES_HEADERS = [
    "case_id",
    "title",
    "case_type",
    "status",
    "location",
    "assigned_officer",
    "complaint_brief",
    "field_report_brief",
    "layout_a_status",
    "field_report_status",
    "layout_b1_revenue_status",
    "layout_b2_ghmc_status",
    "layout_b3_water_status",
    # ---- Extended enforcement tracking columns (new) ----
    "joint_survey_status",
    "objection_status",
    "stay_order_status",
    "original_permitting_officer",
    "land_saved_value",
    "land_type",
    "resolution_brief",
    "created_at",
    # ---- Document Checklist Grid metadata (existing addition) ----
    # link_col is now redundant with *_status holding the URL directly, but
    # is kept (and still populated) for backwards-compatible schemas/tools
    # that read it directly.
    "layout_a_link",
    "layout_a_uploader",
    "layout_a_department",
    "field_report_link",
    "field_report_uploader",
    "field_report_department",
    "layout_b1_revenue_link",
    "layout_b1_revenue_uploader",
    "layout_b1_revenue_department",
    "layout_b2_ghmc_link",
    "layout_b2_ghmc_uploader",
    "layout_b2_ghmc_department",
    "layout_b3_water_link",
    "layout_b3_water_uploader",
    "layout_b3_water_department",
    # ---- AI Notice Generator metadata (new) ----
    "notice_recipient_name",
    "notice_recipient_address",
    "notice_violation_details",
    "notice_draft_text",
    "notice_pdf_link",
]

AUDIT_HEADERS = ["timestamp", "case_id", "user_name", "user_role", "department", "action"]

UNASSIGNED_HEADERS = [
    "scan_id",
    "original_filename",
    "drive_file_id",
    "drive_link",
    "uploader_name",
    "uploader_department",
    "uploaded_at",
    "status",  # "Pending" | "Resolved"
    "notes",
]

VALID_ROLES = {"Operator", "Head"}

DRIVE_ROOT_FOLDER = "HYDRA_Cases"
DRIVE_SUBFOLDERS = ["Layouts_and_Field_Reports", "Approved_Notices"]
UNASSIGNED_DRIVE_FOLDER = "Unassigned_Scans"

# Sub-folder names accepted by the Apps Script "uploadFile" action.
UPLOAD_SUBFOLDER_LAYOUTS = "Layouts_and_Field_Reports"
UPLOAD_SUBFOLDER_NOTICES = "Approved_Notices"

CASE_TYPES = [
    "Encroachment",
    "Illegal Layout",
    "Water Body Violation",
    "Drainage Obstruction",
    "Unauthorized Construction",
    "Other",
]

LAND_TYPES = ["Government", "Private", "FTL (Full Tank Level)", "Buffer Zone", "Unclassified"]

# Default status value used for the new extended enforcement tracking
# columns (joint_survey_status, objection_status, stay_order_status) when a
# case is first created — mirrors the "Pending" default already used for
# the five document-checklist status columns.
ENFORCEMENT_TRACKING_DEFAULT_STATUS = "Pending"

# "Inspected" (set automatically once the AI Field Findings Brief is generated)
# and "Notice Served" (set once the Head approves & signs off a notice) are new
# additions; every pre-existing status value is left untouched.
STATUS_OPTIONS = [
    "New Complaint",
    "Field Verification Pending",
    "Survey Pending",
    "Field Verification Complete",
    "Inspected",
    "Layout Under Review",
    "Notice Issued",
    "Notice Served",
    "Resolved",
    "Closed",
]

# Statuses that count towards the Head dashboard's "Cases Pending Field Survey" KPI.
PENDING_FIELD_SURVEY_STATUSES = {"New Complaint", "Field Verification Pending", "Survey Pending"}

# Land type options specifically required on the Head's "Close Case" form.
CLOSE_CASE_LAND_TYPES = ["FTL/Lake Bed", "Lake Buffer Zone", "Public Park", "Govt Land"]
CLOSE_CASE_LAND_TYPE_PLACEHOLDER = "-- Select land type --"

# Departments an Operator can upload on behalf of.
UPLOAD_DEPARTMENTS = ["HYDRA Field", "HYDRA MRO", "GHMC", "Irrigation"]

# Statuses that a case must reach before it is eligible for AI Notice drafting.
NOTICE_ELIGIBLE_STATUSES = {"Inspected"}

# Statuses that must never be silently overwritten by the automatic
# "Inspected" transition triggered by the Field Findings Brief.
NOTICE_STATUS_LOCK = {"Closed", "Notice Served"}

# The five mandatory documents that make up the Document Checklist Grid.
# `keywords` are lowercase substrings searched for in an uploaded filename to
# auto-detect which document a file represents.
DOCUMENT_TYPES = {
    "layout_a": {
        "label": "Layout A (Physical Field Layout)",
        "status_col": "layout_a_status",
        "link_col": "layout_a_link",
        "uploader_col": "layout_a_uploader",
        "department_col": "layout_a_department",
        "keywords": ["layout_a", "layouta", "layout-a", "physical_layout", "field_layout"],
    },
    "field_report": {
        "label": "Field Inspection Report",
        "status_col": "field_report_status",
        "link_col": "field_report_link",
        "uploader_col": "field_report_uploader",
        "department_col": "field_report_department",
        "keywords": ["field_report", "fieldreport", "inspection_report", "field_inspection"],
    },
    "layout_b1_revenue": {
        "label": "Revenue Record / Local MRO Land Sheet (Layout B1)",
        "status_col": "layout_b1_revenue_status",
        "link_col": "layout_b1_revenue_link",
        "uploader_col": "layout_b1_revenue_uploader",
        "department_col": "layout_b1_revenue_department",
        "keywords": ["b1", "revenue", "mro", "land_sheet", "landsheet"],
    },
    "layout_b2_ghmc": {
        "label": "Municipal / GHMC Layout Approval (Layout B2)",
        "status_col": "layout_b2_ghmc_status",
        "link_col": "layout_b2_ghmc_link",
        "uploader_col": "layout_b2_ghmc_uploader",
        "department_col": "layout_b2_ghmc_department",
        "keywords": ["b2", "ghmc", "municipal"],
    },
    "layout_b3_water": {
        "label": "Water Body / FTL Map (Layout B3)",
        "status_col": "layout_b3_water_status",
        "link_col": "layout_b3_water_link",
        "uploader_col": "layout_b3_water_uploader",
        "department_col": "layout_b3_water_department",
        "keywords": ["b3", "water", "ftl", "water_body", "waterbody"],
    },
}

# Order in which the checklist grid rows are displayed.
DOCUMENT_TYPE_ORDER = ["layout_a", "field_report", "layout_b1_revenue", "layout_b2_ghmc", "layout_b3_water"]

CASE_ID_REGEX = re.compile(r"HYDRA-\d{8}-[A-F0-9]{6}", re.IGNORECASE)

ALLOWED_UPLOAD_EXTENSIONS = ["pdf", "jpg", "jpeg", "png"]

GEMINI_MODEL_NAME = "gemini-1.5-flash"

# Shared warning shown by every AI-briefing/drafting fallback path — see the
# crash-proofing note in the module docstring above.
AI_FALLBACK_WARNING = (
    "AI briefing generation failed or is not configured. Saving case with a "
    "manual/default summary."
)

# Number of characters kept from raw complaint text when Gemini is
# unavailable and we fall back to a manual/default complaint_brief.
MANUAL_BRIEF_TEXT_SLICE_LENGTH = 150


# --------------------------------------------------------------------------------
# GOOGLE API CLIENTS
# --------------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def get_credentials():
    """Load service account credentials from st.secrets, if configured.

    Returns None (never raises or calls st.stop()) when the
    `[gcp_service_account]` block isn't present yet, or when it fails to
    parse. Google Drive (legacy service-account path) is an OPTIONAL
    feature — the app must keep running without it, so every caller of
    this function has to handle a None return gracefully rather than
    assuming credentials always exist."""
    if "gcp_service_account" not in st.secrets:
        return None
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        return Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    except Exception:  # noqa: BLE001
        return None


@st.cache_resource(show_spinner=False)
def get_drive_service():
    """Build the Drive client, or return None if no service account is
    configured (or it fails to build) — this is the single seam every
    legacy Drive-dependent feature (currently: the Pending Review Queue's
    file-move step) checks before doing anything. Returning None here must
    never crash the app; screens that need it show a
    `drive_not_configured_notice()` and disable themselves instead."""
    creds = get_credentials()
    if creds is None:
        return None
    try:
        return build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception:  # noqa: BLE001
        return None


def drive_not_configured_notice():
    """Shared message shown by any screen that needs the legacy Google
    Drive service-account path but doesn't have one configured yet."""
    st.warning(
        "Google Drive (service account) isn't connected yet — add a "
        "`[gcp_service_account]` block to `secrets.toml` to enable this. "
        "See the setup instructions at the top of app.py (step 3). File "
        "uploads elsewhere in the app (Bulk Upload, New Case, Notices) use "
        "the Apps Script uploader instead and don't need this.",
        icon="📁",
    )


@st.cache_resource(show_spinner=False)
def get_gemini_model():
    """Return a configured Gemini 1.5 Flash model, or None if the
    `google-generativeai` package isn't installed, no API key is configured
    in secrets.toml, or the client fails to initialize (bad key, network
    issue, etc). Every caller must handle a None return gracefully — and,
    on top of that, every caller also wraps its actual `generate_content()`
    call in its own try/except, since a model object can still be returned
    here successfully and then fail later at call time (expired/invalid key,
    quota errors, transient API errors, etc)."""
    if not GEMINI_AVAILABLE:
        return None
    gemini_secrets = st.secrets.get("gemini") if hasattr(st, "secrets") else None
    if not gemini_secrets or not gemini_secrets.get("api_key"):
        return None
    try:
        genai.configure(api_key=gemini_secrets["api_key"])
        return genai.GenerativeModel(GEMINI_MODEL_NAME)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------------
# SHEET / DATABASE HELPERS
# --------------------------------------------------------------------------------
# Credential-free replacement for the old gspread/service-account layer:
#   - READS go straight to the public CSV export of each tab in the shared
#     Google Sheet (`read_sheet`) via pandas.
#   - WRITES come in two shapes now:
#       * Brand-new rows (`write_sheet`) are sent with action "writeRow" as
#         a flat, position-based ARRAY, pre-ordered client-side (via
#         dict_to_ordered_row()) to match the target tab's header row
#         exactly — e.g. CASES_HEADERS for HYDRA_Cases, AUDIT_HEADERS for
#         Audit_Logs, UNASSIGNED_HEADERS for Unassigned_Scans. This matches
#         Google Sheets' appendRow(), which itself expects a flat array of
#         values, not a keyed object.
#       * In-place field updates (`update_sheet_row`) are still sent as a
#         partial OBJECT (column_name -> value) with action "update", since
#         only some columns change and every other existing column must be
#         left untouched; that only works if the Apps Script side can look
#         updates up by column NAME, which requires a dict, not a
#         position-based array.
#   - FILE UPLOADS (`upload_bytes_via_apps_script` /
#     `upload_file_via_apps_script`) POST a base64-encoded file with action
#     "uploadFile" and get back {"ok": true, "file_url": "..."}.
#
# Every other function in this file (load_whitelist, load_cases,
# append_case_row, update_case_fields, append_audit_entry,
# load_unassigned_scans, append_unassigned_scan, update_unassigned_fields)
# keeps its original name and signature — callers throughout the app are
# unaffected by this swap. The `*_ws` parameter names are kept too, but now
# simply hold the plain tab-name string (e.g. "HYDRA_Cases") instead of a
# gspread Worksheet object.


def read_sheet(sheet_name):
    """Read one tab of the shared Google Sheet as a DataFrame via its public
    CSV export — no credentials required. The Sheet must be shared as
    "Anyone with the link can view"."""
    try:
        url = f"{st.secrets['DATABASE_URL']}/gviz/tq?tqx=out:csv&sheet={sheet_name}"
        return pd.read_csv(url)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to read '{sheet_name}' tab: {exc}")
        return pd.DataFrame()


def dict_to_ordered_row(row_dict, headers):
    """Convert a dict of column_name -> value into a flat list of values,
    ordered to exactly match `headers` (e.g. CASES_HEADERS, AUDIT_HEADERS,
    or UNASSIGNED_HEADERS). Missing keys become "" so the row always has
    the correct number of positional columns for Google Sheets'
    appendRow(). Values are stringified defensively (None -> ""), since the
    JSON payload is going straight into a spreadsheet cell.
    """
    row = []
    for col in headers:
        value = row_dict.get(col, "")
        row.append("" if value is None else value)
    return row


def write_sheet(sheet_name, row_values):
    """Append a new row to a tab of the shared Google Sheet by POSTing to
    the Apps Script Web App with action "writeRow".

    `row_values` MUST be a flat, position-based list of values, already
    ordered to match the target tab's header row exactly (build it with
    dict_to_ordered_row(row_dict, CASES_HEADERS) / AUDIT_HEADERS /
    UNASSIGNED_HEADERS before calling this). Google Sheets' underlying
    appendRow() call takes a flat array, not a keyed object, so the
    ordering is done here on the Python side rather than relying on the
    Apps Script side to map column names — see the module docstring's
    SETUP INSTRUCTIONS (step 2c) for the matching Apps Script `doPost`
    handler.
    """
    payload = {"action": "writeRow", "sheetName": sheet_name, "rowData": row_values}
    try:
        response = requests.post(st.secrets["WRITE_API_URL"], json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to write a new row to '{sheet_name}': {exc}")
        return None


def update_sheet_row(sheet_name, match_column, match_value, updates):
    """Update the first existing row where `match_column` == `match_value`
    in a tab of the shared Google Sheet by POSTing to the same Apps Script
    Web App, preserving any columns not present in `updates`.

    Unlike write_sheet() (new rows, action "writeRow"), `updates` here
    stays a dict/object of column_name -> value, deliberately NOT
    converted to a positional array: updates are partial by nature (only a
    handful of columns change at a time), so the Apps Script side needs
    the column NAME to know which single cell to overwrite, and every
    other existing column must be left exactly as-is.
    """
    payload = {
        "action": "update",
        "sheetName": sheet_name,
        "matchColumn": match_column,
        "matchValue": match_value,
        "rowData": updates,
    }
    try:
        response = requests.post(st.secrets["WRITE_API_URL"], json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to update '{sheet_name}' row where {match_column}={match_value}: {exc}")
        return None


def upload_bytes_via_apps_script(case_id, file_bytes, filename, mime_type, sub_folder):
    """
    Converts file bytes to Base64 and uploads the actual file to Google Drive 
    using the Apps Script Web App. Returns the real Google Drive URL.
    """
    import base64
    try:
        # 1. Convert raw bytes to Base64 string for transmission
        base64_str = base64.b64encode(file_bytes).decode("utf-8")
        
        # 2. Build the payload matching the Apps Script exact action keys
        payload = {
            "action": "uploadFile",
            "caseId": case_id,
            "fileName": filename,
            "fileBytes": base64_str,
            "mimeType": mime_type,
            "subFolder": sub_folder
        }
        
        # 3. POST to Google Apps Script
        response = requests.post(st.secrets["WRITE_API_URL"], json=payload, timeout=45)
        response.raise_for_status()
        res_data = response.json()
        
        if res_data.get("status") == "success":
            return res_data.get("file_url")
        else:
            st.error(f"Google Drive Upload Error: {res_data.get('message')}")
            return None
    except Exception as e:
        st.error(f"Failed to connect to Google Drive: {e}")
        return None



def upload_file_via_apps_script(case_id, uploaded_file, sub_folder):
    """Convenience wrapper around upload_bytes_via_apps_script() for a
    Streamlit `UploadedFile` object (e.g. from st.file_uploader()).

    Reads the file as bytes with `uploaded_file.read()`, base64-encodes it,
    and POSTs it to the Apps Script "uploadFile" action. Returns a tuple
    (file_url, file_bytes) so callers that also need the raw bytes for
    something else (e.g. running the AI Field Findings Brief in-memory on
    the same upload, with no re-download round trip) don't have to re-read
    the file object a second time.

    Returns (None, None) if the file couldn't be read or the upload failed
    (an st.error() is shown either way).
    """
    try:
        file_bytes = uploaded_file.read()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not read '{getattr(uploaded_file, 'name', 'file')}': {exc}")
        return None, None

    file_url = upload_bytes_via_apps_script(
        case_id, file_bytes, uploaded_file.name, uploaded_file.type, sub_folder
    )
    return file_url, file_bytes


def init_sheets():
    """No credentials or setup API calls needed anymore. Just confirm the
    two required secrets are configured, then hand back the four tab-name
    strings used throughout the app (these used to be gspread Worksheet
    objects; every downstream function now accepts either interchangeably
    since it just forwards the value to read_sheet/write_sheet/update_sheet_row)."""
    missing = [key for key in ("DATABASE_URL", "WRITE_API_URL") if key not in st.secrets]
    if missing:
        st.error(
            "Missing " + ", ".join(f"`{m}`" for m in missing) + " in secrets.toml. "
            "See the setup instructions at the top of app.py."
        )
        st.stop()
    return WHITELIST_SHEET_NAME, CASES_SHEET_NAME, AUDIT_SHEET_NAME, UNASSIGNED_SHEET_NAME


def load_whitelist(whitelist_ws):
    """Return dict keyed by lowercase gmail_id -> row dict."""
    df = read_sheet(whitelist_ws)
    if df.empty:
        return {}
    df = df.fillna("")
    result = {}
    for _, row in df.iterrows():
        row_dict = row.to_dict()
        gmail = str(row_dict.get("gmail_id", "")).strip().lower()
        if gmail:
            result[gmail] = row_dict
    return result


def load_cases(cases_ws):
    df = read_sheet(cases_ws)
    if df.empty:
        return []
    for col in CASES_HEADERS:
        if col not in df.columns:
            df[col] = ""
    df = df.fillna("")
    return df.to_dict("records")


def append_case_row(cases_ws, case_dict):
    row_values = dict_to_ordered_row(case_dict, CASES_HEADERS)
    write_sheet(cases_ws, row_values)


def append_audit_entry(audit_ws, case_id, user_name, user_role, department, action):
    row_dict = {
        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "case_id": case_id,
        "user_name": user_name,
        "user_role": user_role,
        "department": department,
        "action": action,
    }
    row_values = dict_to_ordered_row(row_dict, AUDIT_HEADERS)
    result = write_sheet(audit_ws, row_values)
    if result is None:
        # Auditing must never crash the app; surface as a non-blocking warning.
        st.warning("Could not write audit log entry.")


def update_case_fields(cases_ws, case_id, updates):
    """
    Partially update a case row identified by case_id, preserving any
    columns not present in `updates`. Raises ValueError if the update
    request fails (e.g. the case isn't found by the Apps Script backend).
    """
    result = update_sheet_row(cases_ws, "case_id", case_id, updates)
    if result is None:
        raise ValueError(f"Case '{case_id}' could not be updated in {cases_ws}.")


def load_unassigned_scans(unassigned_ws):
    df = read_sheet(unassigned_ws)
    if df.empty:
        return []
    for col in UNASSIGNED_HEADERS:
        if col not in df.columns:
            df[col] = ""
    df = df.fillna("")
    return df.to_dict("records")


def append_unassigned_scan(unassigned_ws, scan_dict):
    row_values = dict_to_ordered_row(scan_dict, UNASSIGNED_HEADERS)
    write_sheet(unassigned_ws, row_values)


def update_unassigned_fields(unassigned_ws, scan_id, updates):
    result = update_sheet_row(unassigned_ws, "scan_id", scan_id, updates)
    if result is None:
        raise ValueError(f"Scan '{scan_id}' could not be updated in {unassigned_ws}.")


# --------------------------------------------------------------------------------
# GOOGLE DRIVE FOLDER HELPERS (LEGACY SERVICE-ACCOUNT PATH)
# --------------------------------------------------------------------------------
# Everything in this section is now only used by the Pending Review Queue's
# "move an Unassigned_Scans file into the right case folder" step, and by
# the Unassigned_Scans upload path itself (which still needs a Drive
# location to dump unmatched/unreadable files into, since the Apps Script
# "uploadFile" action is case-scoped). Direct, case-scoped uploads
# elsewhere in the app (Bulk Upload matches, New Case complaint doc,
# Approved Notice PDFs) go through upload_bytes_via_apps_script() /
# upload_file_via_apps_script() above instead and don't touch this section.

def _escape_drive_query_value(value):
    return value.replace("'", "\\'")


def get_or_create_drive_folder(drive_service, name, parent_id=None):
    """Idempotently fetch or create a folder by name under an optional parent."""
    safe_name = _escape_drive_query_value(name)
    query = (
        f"mimeType='application/vnd.google-apps.folder' "
        f"and name='{safe_name}' and trashed=false"
    )
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = (
        drive_service.files()
        .list(q=query, spaces="drive", fields="files(id, name)")
        .execute()
    )
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        metadata["parents"] = [parent_id]
    folder = drive_service.files().create(body=metadata, fields="id").execute()
    return folder["id"]


def create_case_folder_structure(drive_service, case_id):
    """
    Creates:
      HYDRA_Cases/<case_id>/Layouts_and_Field_Reports/
      HYDRA_Cases/<case_id>/Approved_Notices/
    Returns the case folder's Drive file ID.
    """
    root_id = get_or_create_drive_folder(drive_service, DRIVE_ROOT_FOLDER)
    case_folder_id = get_or_create_drive_folder(drive_service, case_id, parent_id=root_id)
    for subfolder in DRIVE_SUBFOLDERS:
        get_or_create_drive_folder(drive_service, subfolder, parent_id=case_folder_id)
    return case_folder_id


def get_case_layouts_folder_id(drive_service, case_id):
    """Fetch (creating if necessary) the case's Layouts_and_Field_Reports folder id."""
    root_id = get_or_create_drive_folder(drive_service, DRIVE_ROOT_FOLDER)
    case_folder_id = get_or_create_drive_folder(drive_service, case_id, parent_id=root_id)
    return get_or_create_drive_folder(
        drive_service, "Layouts_and_Field_Reports", parent_id=case_folder_id
    )


def get_unassigned_scans_folder_id(drive_service):
    return get_or_create_drive_folder(drive_service, UNASSIGNED_DRIVE_FOLDER)


def _guess_mime_type(filename):
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return {
        "pdf": "application/pdf",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
    }.get(ext, "application/octet-stream")


def upload_bytes_to_drive_folder(drive_service, file_bytes, filename, parent_folder_id):
    """Upload raw bytes as a new Drive file inside parent_folder_id.
    Makes the file link-shareable (anyone with the link can view) so Head /
    Operator can click straight through to preview it from the checklist grid.
    Returns (file_id, web_view_link).
    """
    media = MediaIoBaseUpload(
        io.BytesIO(file_bytes), mimetype=_guess_mime_type(filename), resumable=False
    )
    metadata = {"name": filename, "parents": [parent_folder_id]}
    created = (
        drive_service.files()
        .create(body=metadata, media_body=media, fields="id, webViewLink")
        .execute()
    )
    file_id = created["id"]

    try:
        drive_service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            fields="id",
        ).execute()
    except Exception:  # noqa: BLE001
        # Non-fatal: the file still exists and is reachable by anyone who
        # already has access to the shared Drive location.
        pass

    link = created.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
    return file_id, link


def move_drive_file(drive_service, file_id, new_parent_id, old_parent_id=None):
    """Move a Drive file into a new parent folder, removing it from its old
    parent(s) so it doesn't stay double-listed."""
    if old_parent_id is None:
        current = drive_service.files().get(fileId=file_id, fields="parents").execute()
        old_parent_id = ",".join(current.get("parents", []))
    drive_service.files().update(
        fileId=file_id,
        addParents=new_parent_id,
        removeParents=old_parent_id,
        fields="id, parents",
    ).execute()


# --------------------------------------------------------------------------------
# FILE-NAME PARSING / READABILITY HELPERS (Bulk Upload Auto-Sorter)
# --------------------------------------------------------------------------------

def extract_case_id_from_filename(filename, known_case_ids):
    """Try to find a Case ID inside a filename.
    1. Exact/substring match (case-insensitive) against every known case_id.
    2. Fallback to the generic HYDRA-YYYYMMDD-XXXXXX pattern in case the case
       isn't in `known_case_ids` yet for some reason.
    Returns the matching case_id (in its canonical stored form) or None.
    """
    lower_name = filename.lower()
    for case_id in known_case_ids:
        if case_id and case_id.lower() in lower_name:
            return case_id

    match = CASE_ID_REGEX.search(filename)
    if match:
        found = match.group(0).upper()
        for case_id in known_case_ids:
            if case_id.upper() == found:
                return case_id
        return found  # matched the pattern but not a known case; caller can decide
    return None


def detect_document_type_from_filename(filename):
    """Return the DOCUMENT_TYPES key whose keywords appear in the filename,
    or None if nothing matches."""
    lower_name = filename.lower()
    for doc_key, config in DOCUMENT_TYPES.items():
        for keyword in config["keywords"]:
            if keyword in lower_name:
                return doc_key
    return None


def is_image_blurry(file_bytes, threshold=8.0):
    """Best-effort blur heuristic using only Pillow (no numpy/opencv
    dependency): run an edge-detection filter and look at the resulting
    pixel-intensity spread. Low spread ~= few sharp edges ~= likely blurry.
    Returns True (treat as blurry/unreadable) if Pillow isn't installed or
    the image can't be analyzed, so unclear files always fall through to
    manual review rather than being silently mis-filed.
    """
    try:
        from PIL import Image, ImageFilter, ImageStat
    except ImportError:
        return False  # Pillow not installed: skip the heuristic, don't block uploads

    try:
        img = Image.open(io.BytesIO(file_bytes)).convert("L")
        edges = img.filter(ImageFilter.FIND_EDGES)
        stat = ImageStat.Stat(edges)
        sharpness = stat.stddev[0]
        return sharpness < threshold
    except Exception:  # noqa: BLE001
        return True


def is_file_readable_and_clean(filename, file_bytes):
    """Best-effort corruption/blur check.
    - Empty files are always rejected.
    - PDFs: must start with the %PDF magic bytes; if PyPDF2 is available,
      also confirm the page table parses.
    - Images (jpg/jpeg/png): must open with Pillow (if installed) and must
      not trip the blur heuristic above.
    - Anything else: accepted as long as it is non-empty (readability of
      arbitrary formats is out of scope for this checker).
    """
    if not file_bytes:
        return False

    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

    if ext == "pdf":
        if not file_bytes.startswith(b"%PDF"):
            return False
        try:
            import PyPDF2

            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            _ = len(reader.pages)
            return True
        except ImportError:
            return True  # PyPDF2 not installed: magic-byte check is enough
        except Exception:  # noqa: BLE001
            return False

    if ext in ("jpg", "jpeg", "png"):
        try:
            from PIL import Image

            img = Image.open(io.BytesIO(file_bytes))
            img.verify()
        except ImportError:
            return True
        except Exception:  # noqa: BLE001
            return False
        return not is_image_blurry(file_bytes)

    return True


# --------------------------------------------------------------------------------
# DUAL AI BRIEFING (Gemini 1.5 Flash) — CRASH-PROOF
# --------------------------------------------------------------------------------
# Every function in this section follows the same contract:
#   - It NEVER raises. Any failure (Gemini not installed, no API key, bad
#     key, network error, quota error, empty response, etc.) is caught here.
#   - On failure it shows a single, mild st.warning() with the exact text
#     "AI briefing generation failed or is not configured. Saving case with
#     a manual/default summary." (AI_FALLBACK_WARNING), then returns a
#     manual/default string instead of AI-generated text.
#   - Callers (case creation, bulk upload, pending review, notice
#     generation) can therefore always trust that these functions return
#     *something* usable and keep going straight to writing to the Google
#     Sheet — AI availability never blocks a save.
#   - generate_field_report_brief() takes the raw in-memory bytes of the
#     just-uploaded file directly (the same bytes handed to
#     upload_bytes_via_apps_script() / upload_file_via_apps_script()), so
#     there is no re-download round trip needed to run Briefing 2.

def generate_complaint_brief(model, raw_text=None, file_bytes=None, filename=None):
    """Briefing 1: read the raw complaint text and/or an attached complaint
    document (PDF or image) and return a structured summary suitable for the
    case's `complaint_brief` field.

    Crash-proof fallback: if Gemini isn't configured, or the API call fails
    for any reason (missing/invalid credentials, network error, quota, empty
    response, etc.), show a mild warning and fall back to the first
    MANUAL_BRIEF_TEXT_SLICE_LENGTH characters of the raw complaint text —
    case creation always proceeds and the row is still written to the sheet.
    """
    raw_text = raw_text or ""
    manual_fallback = raw_text.strip()[:MANUAL_BRIEF_TEXT_SLICE_LENGTH] or (
        "[No complaint text provided — manual review required]"
    )

    if model is None:
        st.warning(AI_FALLBACK_WARNING)
        return manual_fallback

    instruction = (
        "You are assisting a HYDRA (Hyderabad Disaster Response and Asset Protection "
        "Agency) case officer. Read the citizen complaint below (and/or the attached "
        "complaint document, which may be a scan) and produce a concise, structured "
        "brief as short bullet points covering: (1) nature of the complaint, "
        "(2) alleged location / landmark details, (3) type of violation alleged "
        "(e.g. encroachment, illegal layout, water body violation), and "
        "(4) any urgency indicators. Do not invent facts that are not present in "
        "the source material. Return plain text only, no markdown headers."
    )
    parts = [instruction]
    if file_bytes and filename:
        parts.append({"mime_type": _guess_mime_type(filename), "data": file_bytes})
    if raw_text.strip():
        parts.append(f"Complaint text:\n{raw_text.strip()}")

    try:
        response = model.generate_content(parts)
        text = (getattr(response, "text", "") or "").strip()
        if not text:
            raise ValueError("Gemini returned an empty response")
        return text
    except Exception:  # noqa: BLE001 — any Gemini/credential/network error lands here
        st.warning(AI_FALLBACK_WARNING)
        return manual_fallback


def generate_field_report_brief(model, file_bytes, filename):
    """Briefing 2: read a Field Inspection Report file (typed or handwritten
    scan, PDF or image) directly from in-memory bytes — the same bytes that
    were just base64-encoded and POSTed to the Apps Script "uploadFile"
    action — and return a concise 'Field Findings Brief'. No re-download
    from Drive is needed.

    Crash-proof fallback: if Gemini isn't configured, or the API call fails
    for any reason, show a mild warning and fall back to a manual/default
    summary noting the file was received and needs manual review — the
    upload/assignment flow always proceeds and the checklist grid is still
    updated.
    """
    manual_fallback = (
        f"[Manual review required] Field Inspection Report '{filename}' was "
        "received, but an AI-generated Field Findings Brief could not be produced. "
        "An officer should review the attached file directly."
    )

    if model is None:
        st.warning(AI_FALLBACK_WARNING)
        return manual_fallback

    instruction = (
        "You are assisting a HYDRA (Hyderabad Disaster Response and Asset Protection "
        "Agency) case officer. The attached field inspection report may be typed or "
        "handwritten, and may be a scanned image or PDF. Read it carefully and produce "
        "a concise 'Field Findings Brief' as short bullet points covering: what the "
        "inspecting officer observed on-site, whether the alleged violation was "
        "confirmed, any measurements or extent of encroachment noted, and any "
        "recommended next action. If any part of the document is illegible, note that "
        "explicitly rather than guessing at its content. Return plain text only, no "
        "markdown headers."
    )
    parts = [instruction, {"mime_type": _guess_mime_type(filename), "data": file_bytes}]

    try:
        response = model.generate_content(parts)
        text = (getattr(response, "text", "") or "").strip()
        if not text:
            raise ValueError("Gemini returned an empty response")
        return text
    except Exception:  # noqa: BLE001 — any Gemini/credential/network error lands here
        st.warning(AI_FALLBACK_WARNING)
        return manual_fallback


def apply_field_report_brief_to_case(model, cases_ws, audit_ws, case_id, file_bytes, filename, user):
    """Run Briefing 2 for a case's Field Inspection Report, save the result
    into `field_report_brief`, and advance status to 'Inspected' unless the
    case has already progressed past that point (e.g. Closed / Notice
    Served). Never raises — failures are surfaced via st.warning() /
    generate_field_report_brief()'s own fallback instead of blocking the
    upload flow, and the checklist/status update always still runs.

    `file_bytes` is the same in-memory byte string that was (or is about
    to be) base64-encoded and POSTed via upload_bytes_via_apps_script() —
    Gemini reads it directly, with no re-download from Drive needed."""
    brief_text = generate_field_report_brief(model, file_bytes, filename)

    updates = {"field_report_brief": brief_text}
    try:
        cases_now = {c.get("case_id"): c for c in load_cases(cases_ws)}
        current_status = str(cases_now.get(case_id, {}).get("status", "")).strip()
    except Exception:  # noqa: BLE001
        current_status = ""

    status_advanced = False
    if current_status not in NOTICE_STATUS_LOCK:
        updates["status"] = "Inspected"
        status_advanced = True

    try:
        update_case_fields(cases_ws, case_id, updates)
    except ValueError as exc:
        st.warning(f"Could not save AI field findings brief: {exc}")
        return

    action = "AI Field Findings Brief generated"
    if status_advanced:
        action += " (status -> Inspected)"
    append_audit_entry(
        audit_ws,
        case_id=case_id,
        user_name=user["name"],
        user_role=user["role"],
        department=user["department"],
        action=action,
    )


# --------------------------------------------------------------------------------
# AUTHENTICATION
# --------------------------------------------------------------------------------

def google_oauth_login():
    """
    PLACEHOLDER for real Google OAuth Sign-In.

    To implement this for production:
      1. Register an OAuth 2.0 Client ID (Web application) in Google Cloud Console.
      2. Add its client_id / client_secret / redirect_uri to st.secrets["google_oauth"].
      3. Use a library such as `streamlit-oauth` or `authlib` to run the
         Authorization Code flow:
           - Redirect the user to Google's consent screen.
           - Exchange the returned `code` for tokens at the token endpoint.
           - Decode the ID token (or call the userinfo endpoint) to get the
             signed-in user's verified email address.
      4. Pass that verified email into `authenticate_email()` below instead of
         the manually-typed email used by the demo login form.

    This function intentionally does not perform a live network call; it is a
    documented seam for wiring up real OAuth without changing the rest of the
    authentication/authorization logic.
    """
    raise NotImplementedError(
        "Google OAuth Sign-In is not wired up yet. Use the demo login form, "
        "or implement this function per the docstring above."
    )


def authenticate_email(email, whitelist):
    """Validate an email against the whitelist. Returns the user's row dict or None."""
    if not email:
        return None
    user = whitelist.get(email.strip().lower())
    if not user:
        return None
    if user.get("role") not in VALID_ROLES:
        return None
    return user


def render_login_screen(whitelist_ws):
    st.title(f"🛡️ {APP_TITLE}")
    st.subheader("Secure Sign-In")

    st.info(
        "Google OAuth Sign-In is not yet enabled in this deployment. "
        "Use the whitelisted-email demo login below. See `google_oauth_login()` "
        "in app.py for how to wire up real Google Sign-In.",
        icon="ℹ️",
    )

    with st.form("login_form", clear_on_submit=False):
        email = st.text_input("Gmail address", placeholder="you@gmail.com")
        submitted = st.form_submit_button("Sign in", use_container_width=True)

    if submitted:
        whitelist = load_whitelist(whitelist_ws)
        user = authenticate_email(email, whitelist)
        if user is None:
            st.error("Access denied. This Gmail address is not whitelisted, or has no valid role.")
            return
        st.session_state.authenticated = True
        st.session_state.user = {
            "email": email.strip().lower(),
            "name": user.get("name", ""),
            "role": user.get("role", ""),
            "department": user.get("department", ""),
        }
        st.rerun()

    st.divider()
    if st.button("Sign in with Google (coming soon)", disabled=True, use_container_width=True):
        google_oauth_login()


def logout():
    for key in ("authenticated", "user"):
        st.session_state.pop(key, None)
    st.rerun()


# --------------------------------------------------------------------------------
# CASE CREATION (OPERATOR)
# --------------------------------------------------------------------------------

def generate_case_id():
    today = datetime.utcnow().strftime("%Y%m%d")
    short_id = uuid.uuid4().hex[:6].upper()
    return f"HYDRA-{today}-{short_id}"


def render_new_case_form(cases_ws, audit_ws, drive_service, model, user):
    st.sidebar.header("📁 New Case")
    with st.sidebar.form("new_case_form", clear_on_submit=True):
        title = st.text_input("Case title")
        case_type = st.selectbox("Case type", CASE_TYPES)
        location = st.text_input("Location")
        complaint_raw_text = st.text_area("Raw complaint text / description", height=100)
        complaint_file = st.file_uploader(
            "Complaint document (optional — PDF or photo/scan)",
            type=ALLOWED_UPLOAD_EXTENSIONS,
            key="new_case_complaint_file",
        )
        land_type = st.selectbox("Land type", LAND_TYPES)
        submitted = st.form_submit_button("Create case", use_container_width=True)

    if not submitted:
        return

    if not title.strip() or not location.strip():
        st.sidebar.error("Title and location are required.")
        return

    if not complaint_raw_text.strip() and complaint_file is None:
        st.sidebar.error("Provide either complaint text, a complaint document, or both.")
        return

    case_id = generate_case_id()
    # Read the uploaded complaint file's bytes once, up front, so both the
    # AI complaint briefing below and the Apps Script archival upload can
    # use the same in-memory bytes without needing to re-read the (single-
    # use) file stream a second time.
    complaint_file_bytes = complaint_file.read() if complaint_file is not None else None
    complaint_filename = complaint_file.name if complaint_file is not None else None
    complaint_mime_type = complaint_file.type if complaint_file is not None else None

    with st.spinner(f"Creating case {case_id} and running AI complaint briefing..."):
        # ---- Drive folder scaffolding (legacy service-account path) is ----
        # ---- OPTIONAL: skip cleanly if Drive isn't configured rather ------
        # ---- than blocking case creation. All actual file uploads below ---
        # ---- go through the Apps Script "uploadFile" action instead and --
        # ---- don't need this scaffolding, but it's harmless to keep for --
        # ---- deployments that still rely on the legacy Pending Review ----
        # ---- move-file path. ----------------------------------------------
        if drive_service is not None:
            try:
                create_case_folder_structure(drive_service, case_id)
            except Exception as exc:  # noqa: BLE001
                st.sidebar.warning(f"Legacy Drive folder scaffolding failed (non-blocking): {exc}")

        # ---- Briefing 1: AI Complaint Brief ------------------------------
        # generate_complaint_brief() is fully crash-proof: whether Gemini is
        # unconfigured, mis-configured, or throws at call time, it shows a
        # mild st.warning() itself and returns a manual/default summary
        # (first ~150 characters of the raw complaint text) instead of
        # raising. Case creation below always proceeds regardless of AI
        # availability.
        complaint_brief_text = generate_complaint_brief(
            model,
            raw_text=complaint_raw_text,
            file_bytes=complaint_file_bytes,
            filename=complaint_filename,
        )

        # ---- Archive the raw complaint document via the Apps Script -------
        # ---- "uploadFile" action, if one was attached. This is a plain ----
        # ---- POST of the base64-encoded bytes — no service account -------
        # ---- needed. Non-fatal on failure: the AI brief (or its manual ----
        # ---- fallback) above already captured what mattered. ---------------
        if complaint_file_bytes:
            complaint_file_url = upload_bytes_via_apps_script(
                case_id,
                complaint_file_bytes,
                complaint_filename,
                complaint_mime_type,
                UPLOAD_SUBFOLDER_LAYOUTS,
            )
            if complaint_file_url is None:
                st.sidebar.warning(
                    "Complaint document could not be archived to Drive, but the "
                    "case is still being created with the AI complaint brief "
                    "captured above."
                )

        case_row = {
            "case_id": case_id,
            "title": title.strip(),
            "case_type": case_type,
            "status": "New Complaint",
            "location": location.strip(),
            "assigned_officer": user["name"],
            "complaint_brief": complaint_brief_text,
            "field_report_brief": "",
            "layout_a_status": "Pending",
            "field_report_status": "Pending",
            "layout_b1_revenue_status": "Pending",
            "layout_b2_ghmc_status": "Pending",
            "layout_b3_water_status": "Pending",
            # ---- Extended enforcement tracking columns (new) ----
            "joint_survey_status": ENFORCEMENT_TRACKING_DEFAULT_STATUS,
            "objection_status": ENFORCEMENT_TRACKING_DEFAULT_STATUS,
            "stay_order_status": ENFORCEMENT_TRACKING_DEFAULT_STATUS,
            "original_permitting_officer": "",
            "land_saved_value": "",
            "land_type": land_type,
            "resolution_brief": "",
            "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }

        # ---- Write the case row to the Google Sheet -----------------------
        # This step is intentionally independent of AI availability: by the
        # time we get here, complaint_brief_text is always a valid string
        # (either Gemini's output or the manual/default fallback above), so
        # a missing/broken Gemini key can never prevent the case from being
        # saved. append_case_row() itself now converts this dict into a flat,
        # position-based list (via dict_to_ordered_row(case_row,
        # CASES_HEADERS)) before it's POSTed with action "writeRow",
        # matching Google Sheets' appendRow() contract.
        try:
            append_case_row(cases_ws, case_row)
        except Exception as exc:  # noqa: BLE001
            st.sidebar.error(f"Saving the case row failed: {exc}")
            return

        append_audit_entry(
            audit_ws,
            case_id=case_id,
            user_name=user["name"],
            user_role=user["role"],
            department=user["department"],
            action="Case created (AI complaint brief generated)",
        )

    st.sidebar.success(f"Case {case_id} created successfully.")
    st.cache_data.clear()
    st.rerun()


# --------------------------------------------------------------------------------
# DOCUMENT CHECKLIST GRID (STATUS MATRIX)
# --------------------------------------------------------------------------------

def render_document_checklist_grid(case_row_dict, key_prefix=""):
    """Render the 5-row mandatory-document checklist for one case.
    `case_row_dict` is a plain dict of a single row from HYDRA_Cases (as
    returned by load_cases(), or a pandas Series converted via .to_dict()).

    Each document's status column (e.g. layout_a_status) now holds either
    the literal string "Pending" or the full https:// Drive file URL
    returned by the Apps Script "uploadFile" action. Any value starting
    with "http" is treated as "uploaded", and rendered as a clickable green
    "✅ View File" link straight to that URL so the Head/Operator can open
    the physical file in Drive instantly.
    """
    st.markdown("##### 📑 Document Checklist Grid")

    for doc_key in DOCUMENT_TYPE_ORDER:
        config = DOCUMENT_TYPES[doc_key]
        status = str(case_row_dict.get(config["status_col"], "")).strip()
        uploader = str(case_row_dict.get(config["uploader_col"], "")).strip()
        department = str(case_row_dict.get(config["department_col"], "")).strip()

        uploaded = status.lower().startswith("http")

        row_col1, row_col2 = st.columns([2.2, 3])
        with row_col1:
            if uploaded:
                st.markdown(f"✅ **{config['label']}**")
            else:
                st.markdown(
                    f"<span style='color:#d62728;font-weight:600;'>❌ {config['label']}</span>",
                    unsafe_allow_html=True,
                )
        with row_col2:
            if uploaded:
                by_line = uploader or "Unknown uploader"
                if department:
                    by_line += f" · {department}"
                st.markdown(
                    f"<a href='{status}' target='_blank' "
                    f"style='color:#2ca02c;font-weight:600;text-decoration:none;'>"
                    f"✅ View File</a>  \n*Uploaded by {by_line}*",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    "<span style='color:#d62728;'>Missing — not yet uploaded</span>",
                    unsafe_allow_html=True,
                )
        st.divider()


# --------------------------------------------------------------------------------
# BULK UPLOAD AUTO-SORTER (OPERATOR)
# --------------------------------------------------------------------------------

def process_single_upload(
    cases_ws,
    audit_ws,
    unassigned_ws,
    drive_service,
    filename,
    file_bytes,
    department,
    user,
    known_case_ids,
    model=None,
):
    """Route one uploaded file: either straight into its case's checklist
    grid slot (via the Apps Script "uploadFile" action), or into
    Unassigned_Scans for manual triage (via the legacy Drive service
    account, since that path isn't case-scoped). If the file is a matched
    Field Inspection Report, also triggers Briefing 2 (AI Field Findings
    Brief) directly on the in-memory bytes and advances the case to
    'Inspected'. Returns a short status message string for display in the
    UI, plus a bool indicating success/failure.
    """

    case_id = extract_case_id_from_filename(filename, known_case_ids)
    doc_key = detect_document_type_from_filename(filename)
    readable_and_clean = is_file_readable_and_clean(filename, file_bytes)

    matched = bool(case_id) and case_id in known_case_ids and bool(doc_key) and readable_and_clean

    if matched:
        mime_type = _guess_mime_type(filename)
        file_url = upload_bytes_via_apps_script(
            case_id, file_bytes, filename, mime_type, UPLOAD_SUBFOLDER_LAYOUTS
        )

        if file_url:
            config = DOCUMENT_TYPES[doc_key]
            try:
                update_case_fields(
                    cases_ws,
                    case_id,
                    {
                        # The status column now stores the Drive file URL
                        # directly (checked via "starts with http" in the
                        # checklist grid) instead of the word "Uploaded".
                        config["status_col"]: file_url,
                        config["link_col"]: file_url,
                        config["uploader_col"]: user["name"],
                        config["department_col"]: department,
                    },
                )
            except ValueError as exc:
                return f"⚠️ '{filename}': {exc}", False

            append_audit_entry(
                audit_ws,
                case_id=case_id,
                user_name=user["name"],
                user_role=user["role"],
                department=department,
                action=f"Document uploaded: {config['label']} ('{filename}')",
            )

            result_message = f"✅ '{filename}' → matched to case **{case_id}** as *{config['label']}*."

            # ---- Briefing 2: Field Inspection Report triggers the AI --------
            # ---- Field Findings Brief and advances status to 'Inspected'. ---
            # ---- Reads the same in-memory `file_bytes` that was just --------
            # ---- uploaded — no re-download from Drive needed. Fully ---------
            # ---- crash-proof — see apply_field_report_brief_to_case(). ------
            if doc_key == "field_report":
                apply_field_report_brief_to_case(
                    model, cases_ws, audit_ws, case_id, file_bytes, filename, user
                )
                result_message += " 🤖 Field Findings Brief saved — status advanced to **Inspected**."

            return result_message, True

        # Apps Script upload failed for a file that otherwise matched a
        # case — fall through to Unassigned_Scans below rather than losing
        # the file entirely.
        st.warning(
            f"'{filename}' matched case {case_id} but the upload failed; "
            "routing to Pending Review instead."
        )

    # ---- Unmatched / blurry / unreadable / upload-failed: fall back to -----
    # ---- Unassigned_Scans (still via the legacy Drive service account, -----
    # ---- since this bucket isn't tied to a specific case's Apps-Script------
    # ---- managed subfolder). -------------------------------------------------
    if drive_service is None:
        return (
            f"❌ '{filename}': could not be auto-matched, and Google Drive "
            "(service account) isn't connected to file it to Unassigned_Scans "
            "for manual review.",
            False,
        )

    try:
        unassigned_folder_id = get_unassigned_scans_folder_id(drive_service)
        file_id, link = upload_bytes_to_drive_folder(
            drive_service, file_bytes, filename, unassigned_folder_id
        )
    except Exception as exc:  # noqa: BLE001
        return f"❌ '{filename}': upload failed entirely ({exc}).", False

    scan_id = f"SCAN-{uuid.uuid4().hex[:8].upper()}"
    reasons = []
    if not case_id or case_id not in known_case_ids:
        reasons.append("no matching Case ID found in filename")
    if not doc_key:
        reasons.append("document type could not be detected")
    if not readable_and_clean:
        reasons.append("file unreadable, corrupt, or too blurry")

    append_unassigned_scan(
        unassigned_ws,
        {
            "scan_id": scan_id,
            "original_filename": filename,
            "drive_file_id": file_id,
            "drive_link": link,
            "uploader_name": user["name"],
            "uploader_department": department,
            "uploaded_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "status": "Pending",
            "notes": "; ".join(reasons),
        },
    )
    append_audit_entry(
        audit_ws,
        case_id="",
        user_name=user["name"],
        user_role=user["role"],
        department=department,
        action=f"File routed to Unassigned_Scans: '{filename}' ({'; '.join(reasons)})",
    )
    return f"📥 '{filename}' → could not auto-match ({'; '.join(reasons)}); sent to **Pending Review**.", True


def render_bulk_upload_auto_sorter(cases_ws, audit_ws, unassigned_ws, drive_service, model, user):
    st.subheader("📤 Bulk Upload Auto-Sorter")

    st.caption(
        "Upload multiple files at once. Files whose name contains a Case ID "
        "and a recognizable document type (e.g. `HYDRA-20260731-AB12CD_layout_a.pdf` "
        "or `HYDRA-20260731-AB12CD_ghmc.pdf`) are filed straight into that case's "
        "Document Checklist Grid via the Apps Script uploader. A matched Field "
        "Inspection Report also triggers the AI Field Findings Brief automatically, "
        "reading the file in memory. Anything unmatched, unreadable, or too blurry "
        "is sent to the Pending Review queue instead."
    )

    if drive_service is None:
        st.info(
            "📁 Google Drive (service account) isn't connected — matched uploads "
            "still work via the Apps Script uploader, but any file that can't be "
            "auto-matched won't be able to reach the Unassigned_Scans / Pending "
            "Review queue until it's configured.",
            icon="ℹ️",
        )

    department = st.selectbox("Uploading on behalf of department", UPLOAD_DEPARTMENTS)
    uploaded_files = st.file_uploader(
        "Select files to upload",
        type=ALLOWED_UPLOAD_EXTENSIONS,
        accept_multiple_files=True,
        key="bulk_upload_files",
    )

    if st.button("Process Upload Batch", use_container_width=True, disabled=not uploaded_files):
        known_case_ids = [c.get("case_id", "") for c in load_cases(cases_ws)]
        results = []
        with st.spinner(f"Sorting {len(uploaded_files)} file(s)..."):
            for f in uploaded_files:
                file_bytes = f.getvalue()
                message, ok = process_single_upload(
                    cases_ws,
                    audit_ws,
                    unassigned_ws,
                    drive_service,
                    f.name,
                    file_bytes,
                    department,
                    user,
                    known_case_ids,
                    model=model,
                )
                results.append((message, ok))

        for message, ok in results:
            if ok:
                st.success(message)
            else:
                st.error(message)

        st.cache_data.clear()


# --------------------------------------------------------------------------------
# PENDING REVIEW QUEUE (EXCEPTION HANDLER)
# --------------------------------------------------------------------------------

def render_pending_review_queue(cases_ws, audit_ws, unassigned_ws, drive_service, model, user):
    st.subheader("📥 Pending Review Queue")

    if drive_service is None:
        drive_not_configured_notice()
        return

    st.caption(
        "Files that couldn't be auto-sorted land here. Assign each one to a "
        "Case ID and Document Type to route it into the right Drive folder "
        "and update that case's checklist grid. Assigning a Field Inspection "
        "Report here also triggers the AI Field Findings Brief."
    )

    scans = load_unassigned_scans(unassigned_ws)
    pending_scans = [s for s in scans if str(s.get("status", "")).strip().lower() == "pending"]

    if not pending_scans:
        st.info("No files are currently pending review. 🎉")
        return

    cases = load_cases(cases_ws)
    case_options = {f"{c['case_id']} — {c.get('title', '')}": c["case_id"] for c in cases if c.get("case_id")}
    doc_type_options = {DOCUMENT_TYPES[k]["label"]: k for k in DOCUMENT_TYPE_ORDER}

    for scan in pending_scans:
        scan_id = scan.get("scan_id", "")
        with st.container(border=True):
            info_col, form_col = st.columns([2, 2])

            with info_col:
                st.markdown(f"**{scan.get('original_filename', '(unknown filename)')}**")
                link = scan.get("drive_link", "")
                if link:
                    st.markdown(f"[Open / Preview File]({link})")
                st.caption(
                    f"Uploaded by {scan.get('uploader_name', '—')} "
                    f"({scan.get('uploader_department', '—')}) · "
                    f"{scan.get('uploaded_at', '—')}"
                )
                notes = scan.get("notes", "")
                if notes:
                    st.caption(f"Why it wasn't auto-matched: {notes}")

            with form_col:
                if not case_options:
                    st.warning("No cases exist yet to assign this file to.")
                    continue

                selected_case_label = st.selectbox(
                    "Assign to Case ID",
                    list(case_options.keys()),
                    key=f"review_case_{scan_id}",
                )
                selected_doc_label = st.selectbox(
                    "Document Type",
                    list(doc_type_options.keys()),
                    key=f"review_doctype_{scan_id}",
                )

                if st.button("Assign & Route File", key=f"review_assign_{scan_id}", use_container_width=True):
                    target_case_id = case_options[selected_case_label]
                    doc_key = doc_type_options[selected_doc_label]
                    config = DOCUMENT_TYPES[doc_key]

                    try:
                        target_folder_id = get_case_layouts_folder_id(drive_service, target_case_id)
                        move_drive_file(
                            drive_service, scan.get("drive_file_id", ""), target_folder_id
                        )
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Could not move file in Drive: {exc}")
                        continue

                    scan_link = scan.get("drive_link", "")
                    try:
                        update_case_fields(
                            cases_ws,
                            target_case_id,
                            {
                                # Status column stores the Drive URL directly,
                                # same convention as the Bulk Upload path.
                                config["status_col"]: scan_link,
                                config["link_col"]: scan_link,
                                config["uploader_col"]: scan.get("uploader_name", ""),
                                config["department_col"]: scan.get("uploader_department", ""),
                            },
                        )
                    except ValueError as exc:
                        st.error(str(exc))
                        continue

                    try:
                        update_unassigned_fields(
                            unassigned_ws,
                            scan_id,
                            {
                                "status": "Resolved",
                                "notes": f"Manually assigned to {target_case_id} as {config['label']} by {user['name']}",
                            },
                        )
                    except ValueError as exc:
                        st.error(str(exc))
                        continue

                    append_audit_entry(
                        audit_ws,
                        case_id=target_case_id,
                        user_name=user["name"],
                        user_role=user["role"],
                        department=user["department"],
                        action=(
                            f"Pending review resolved: '{scan.get('original_filename', '')}' "
                            f"assigned as {config['label']}"
                        ),
                    )

                    # ---- Briefing 2 also fires from manual assignment --------
                    # (fully crash-proof — see apply_field_report_brief_to_case)
                    if doc_key == "field_report":
                        try:
                            drive_file_id = scan.get("drive_file_id", "")
                            downloaded = (
                                drive_service.files()
                                .get_media(fileId=drive_file_id)
                                .execute()
                                if drive_file_id
                                else None
                            )
                        except Exception:  # noqa: BLE001
                            downloaded = None
                        if downloaded:
                            apply_field_report_brief_to_case(
                                model,
                                cases_ws,
                                audit_ws,
                                target_case_id,
                                downloaded,
                                scan.get("original_filename", "field_report.pdf"),
                                user,
                            )
                        else:
                            st.warning(
                                "File moved and checklist updated, but the AI Field "
                                "Findings Brief could not be generated (could not "
                                "re-download the file from Drive)."
                            )

                    st.success(f"'{scan.get('original_filename', '')}' assigned to {target_case_id}.")
                    st.cache_data.clear()
                    st.rerun()


# --------------------------------------------------------------------------------
# AI NOTICE GENERATOR WITH QR CODE (HEAD ONLY SIGN-OFF)
# --------------------------------------------------------------------------------

def generate_notice_draft(model, case_row, recipient_name, recipient_address, violation_details):
    """Draft a formal HYDRA show-cause notice using Gemini, following the
    standard HYDRA legal notice structure.

    Crash-proof fallback: if Gemini isn't configured, or the API call fails
    for any reason, show a mild warning and fall back to a manual/default
    placeholder draft built from the fields the Head already typed in — the
    Head can still edit and (once a real draft is written manually) approve
    and sign off the notice; drafting failures never block the workflow.
    """
    manual_fallback = (
        "[MANUAL DRAFT REQUIRED — AI drafting unavailable]\n\n"
        f"Case ID: {case_row.get('case_id', '')}\n"
        f"Case Title: {case_row.get('title', '')}\n"
        f"Date: {date.today().isoformat()}\n\n"
        f"To: {recipient_name}\n"
        f"Address: {recipient_address}\n\n"
        f"Violation details / grounds for notice:\n{violation_details}\n\n"
        "Please replace this placeholder with a formally drafted HYDRA "
        "show-cause notice before approving and signing off."
    )

    if model is None:
        st.warning(AI_FALLBACK_WARNING)
        return manual_fallback

    prompt = f"""You are drafting an official HYDRA (Hyderabad Disaster Response and Asset
Protection Agency) show-cause notice, following the standard HYDRA legal notice
template. Write in a formal, precise administrative-legal register.

Structure the notice with:
1. A reference line with the Case ID and today's date.
2. Addressee block (recipient name and address).
3. A subject line identifying it as a show-cause notice regarding the alleged
   violation.
4. A body explaining the violation observed (drawing on the field findings
   provided below) and the basis for HYDRA's jurisdiction over encroachments,
   unauthorized layouts, or water-body/FTL violations as applicable to this
   case type.
5. A clear directive that the recipient must show cause in writing within 7
   days as to why enforcement action should not be taken.
6. A closing statement noting that failure to respond within the stipulated
   period will result in ex-parte action being taken.

Case ID: {case_row.get('case_id', '')}
Case Title: {case_row.get('title', '')}
Case Type: {case_row.get('case_type', '')}
Location: {case_row.get('location', '')}
Land Type: {case_row.get('land_type', '')}
Recipient Name: {recipient_name}
Recipient Address: {recipient_address}
Violation Details / Field Findings: {violation_details}

Return only the notice text itself, ready to be placed into a formal PDF.
Do not include markdown formatting, code fences, or commentary outside the
notice."""

    try:
        response = model.generate_content(prompt)
        text = (getattr(response, "text", "") or "").strip()
        if not text:
            raise ValueError("Gemini returned an empty response")
        return text
    except Exception:  # noqa: BLE001 — any Gemini/credential/network error lands here
        st.warning(AI_FALLBACK_WARNING)
        return manual_fallback


def render_notice_pdf_bytes(case_id, case_title, notice_text, recipient_name):
    """Render the approved notice text into a formal PDF with a unique
    tracking QR code (encoding the Case ID) embedded in the corner. Raises
    RuntimeError with a clear message if reportlab/qrcode aren't installed."""
    if not REPORTLAB_AVAILABLE:
        raise RuntimeError(
            "The 'reportlab' package is required to render notice PDFs but is not installed."
        )
    if not QRCODE_AVAILABLE:
        raise RuntimeError(
            "The 'qrcode' package is required to embed the tracking QR code but is not installed."
        )

    qr_img = qrcode.make(f"HYDRA-CASE:{case_id}")
    qr_buffer = io.BytesIO()
    qr_img.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)

    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        pdf_buffer,
        pagesize=A4,
        topMargin=22 * mm,
        bottomMargin=22 * mm,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
    )
    styles = getSampleStyleSheet()
    header_style = ParagraphStyle(
        "HydraHeader", parent=styles["Heading1"], alignment=1, fontSize=14, spaceAfter=2
    )
    sub_style = ParagraphStyle(
        "HydraSub", parent=styles["Normal"], alignment=1, fontSize=9, textColor=rl_colors.grey
    )
    body_style = ParagraphStyle(
        "HydraBody", parent=styles["Normal"], fontSize=10.5, leading=15, spaceAfter=8
    )
    meta_style = ParagraphStyle(
        "HydraMeta", parent=styles["Normal"], fontSize=10, leading=14, spaceAfter=4
    )

    story = []
    story.append(Paragraph("HYDRA — Hyderabad Disaster Response and Asset Protection Agency", header_style))
    story.append(Paragraph("Show-Cause Notice", sub_style))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(f"<b>Case ID:</b> {case_id}", meta_style))
    story.append(Paragraph(f"<b>Case Title:</b> {case_title}", meta_style))
    story.append(Paragraph(f"<b>Date Issued:</b> {date.today().isoformat()}", meta_style))
    story.append(Paragraph(f"<b>To:</b> {recipient_name}", meta_style))
    story.append(Spacer(1, 6 * mm))

    for para in (notice_text or "").split("\n"):
        para = para.strip()
        if para:
            # Basic HTML-escaping so stray "<"/"&" in AI output can't break the PDF markup.
            safe_para = para.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(safe_para, body_style))

    story.append(Spacer(1, 14 * mm))
    story.append(RLImage(qr_buffer, width=28 * mm, height=28 * mm))
    story.append(Paragraph(f"Scan to verify — Case ID: {case_id}", sub_style))

    doc.build(story)
    pdf_buffer.seek(0)
    return pdf_buffer.getvalue()


def render_notice_generator(cases_ws, audit_ws, drive_service, model, user):
    st.subheader("📝 AI Notice Generator")
    st.caption(
        "Draft a formal HYDRA show-cause notice for cases that have completed field "
        "inspection. Only Head/Approver accounts can approve and sign off; drafting "
        "does not require sign-off privileges or Google Drive — approval files the "
        "signed PDF via the Apps Script uploader (Approved_Notices subfolder), no "
        "service account needed. If Gemini is unavailable, drafting falls back to "
        "an editable manual placeholder so you can still write and approve a "
        "notice by hand."
    )

    if not REPORTLAB_AVAILABLE or not QRCODE_AVAILABLE:
        st.warning(
            "Notice PDF rendering is unavailable in this environment — install "
            "`reportlab` and `qrcode` to enable it. AI drafting still works."
        )

    cases = load_cases(cases_ws)
    inspected_cases = [
        c for c in cases if str(c.get("status", "")).strip() in NOTICE_ELIGIBLE_STATUSES
    ]

    if not inspected_cases:
        st.info("No cases are currently in 'Inspected' status and eligible for a notice.")
        return

    case_options = {f"{c['case_id']} — {c.get('title', '')}": c for c in inspected_cases}
    selected_label = st.selectbox("Select an inspected case", list(case_options.keys()))
    case_row = case_options[selected_label]
    case_id = case_row["case_id"]

    with st.expander("Field findings (from AI Field Briefing)", expanded=False):
        st.write(case_row.get("field_report_brief", "") or "No field report brief on file yet.")

    st.markdown("#### Recipient & Violation Details")
    recipient_name = st.text_input(
        "Recipient name", value=case_row.get("notice_recipient_name", ""), key=f"notice_recipient_{case_id}"
    )
    recipient_address = st.text_area(
        "Recipient address",
        value=case_row.get("notice_recipient_address", ""),
        key=f"notice_address_{case_id}",
        height=80,
    )
    violation_details = st.text_area(
        "Violation details / grounds for notice",
        value=case_row.get("notice_violation_details", "") or case_row.get("field_report_brief", ""),
        key=f"notice_violation_{case_id}",
        height=120,
    )

    draft_key = f"notice_draft_{case_id}"
    if draft_key not in st.session_state and case_row.get("notice_draft_text"):
        st.session_state[draft_key] = case_row["notice_draft_text"]

    if st.button("✍️ Draft Notice with AI", key=f"draft_btn_{case_id}"):
        if not recipient_name.strip() or not violation_details.strip():
            st.error("Recipient name and violation details are required to draft a notice.")
        else:
            with st.spinner("Drafting notice..."):
                draft = generate_notice_draft(
                    model, case_row, recipient_name.strip(), recipient_address.strip(), violation_details.strip()
                )
            st.session_state[draft_key] = draft

    if st.session_state.get(draft_key):
        st.markdown("#### Draft Notice (editable before sign-off)")
        edited_draft = st.text_area(
            "Notice text", value=st.session_state[draft_key], key=f"notice_edit_{case_id}", height=350
        )
        st.session_state[draft_key] = edited_draft

        st.divider()

        # ---- ROLE PROTECTION ---------------------------------------------
        # Operators/Clerks can draft and review, but only Head/Approver
        # accounts may click Approve and Sign Off. The disabled= flag hides
        # the affordance in the UI; the role check inside the handler is a
        # second, server-side gate that fires even if the UI state is stale.
        is_head = user["role"] == "Head"
        if not is_head:
            st.warning(
                "Only Head/Approver accounts can approve and sign off on notices. "
                "You can prepare and hand off this draft, but sign-off is disabled "
                "for your role."
            )

        approve_disabled = (
            (not is_head)
            or (not recipient_name.strip())
            or (not REPORTLAB_AVAILABLE)
            or (not QRCODE_AVAILABLE)
        )

        if st.button(
            "✅ Approve and Sign Off",
            key=f"approve_btn_{case_id}",
            disabled=approve_disabled,
            use_container_width=True,
        ):
            if user["role"] != "Head":
                # Server-side role gate — never trust the disabled attribute alone.
                st.error("Access denied: only Head/Approver roles may approve notices.")
                return

            with st.spinner("Rendering signed notice PDF and filing it..."):
                try:
                    pdf_bytes = render_notice_pdf_bytes(
                        case_id, case_row.get("title", ""), edited_draft, recipient_name.strip()
                    )
                except RuntimeError as exc:
                    st.error(str(exc))
                    return

                filename = f"{case_id}_Notice.pdf"
                link = upload_bytes_via_apps_script(
                    case_id, pdf_bytes, filename, "application/pdf", UPLOAD_SUBFOLDER_NOTICES
                )
                if link is None:
                    st.error("Notice PDF was rendered, but saving it via the Apps Script uploader failed.")
                    return

                try:
                    update_case_fields(
                        cases_ws,
                        case_id,
                        {
                            "status": "Notice Served",
                            "notice_recipient_name": recipient_name.strip(),
                            "notice_recipient_address": recipient_address.strip(),
                            "notice_violation_details": violation_details.strip(),
                            "notice_draft_text": edited_draft,
                            "notice_pdf_link": link,
                        },
                    )
                except ValueError as exc:
                    st.error(str(exc))
                    return

                append_audit_entry(
                    audit_ws,
                    case_id=case_id,
                    user_name=user["name"],
                    user_role=user["role"],
                    department=user["department"],
                    action=f"Notice approved and signed off (recipient: {recipient_name.strip()}); status -> Notice Served",
                )

            st.success(f"Notice for {case_id} approved, signed off, and filed to Approved_Notices.")
            st.session_state.pop(draft_key, None)
            st.cache_data.clear()
            st.rerun()


# --------------------------------------------------------------------------------
# HEAD ANALYTICS BOARD HELPERS
# --------------------------------------------------------------------------------

def safe_float(value):
    """Best-effort conversion of a sheet cell to float; blanks/garbage -> 0.0."""
    try:
        if value is None or str(value).strip() == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_case_date(value):
    """Parse the ISO-ish created_at string stored by generate_case_id/case_row
    into a plain `date`. Returns None if it can't be parsed."""
    if not value:
        return None
    try:
        cleaned = str(value).strip()
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1]
        return datetime.fromisoformat(cleaned).date()
    except (TypeError, ValueError):
        return None


def get_operator_names(whitelist_ws):
    """Distinct display names of whitelisted Operators, sorted."""
    whitelist = load_whitelist(whitelist_ws)
    names = {
        row.get("name", "").strip()
        for row in whitelist.values()
        if row.get("role") == "Operator" and row.get("name", "").strip()
    }
    return sorted(names)


def cases_to_dataframe(cases):
    """Build a pandas DataFrame from the raw list-of-dicts, guaranteeing all
    CASES_HEADERS columns exist even if the sheet is empty."""
    if cases:
        df = pd.DataFrame(cases)
    else:
        df = pd.DataFrame(columns=CASES_HEADERS)
    for col in CASES_HEADERS:
        if col not in df.columns:
            df[col] = ""
    df["land_saved_value_num"] = df["land_saved_value"].apply(safe_float)
    df["created_date"] = df["created_at"].apply(parse_case_date)
    return df


def render_kpi_cards(df):
    total_cases = len(df)
    total_land_saved = df["land_saved_value_num"].sum()
    pending_field_survey = int(
        df["status"].isin(PENDING_FIELD_SURVEY_STATUSES).sum()
    )

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Cases", f"{total_cases}")
    col2.metric("Total Land Saved", f"{total_land_saved:,.2f}")
    col3.metric("Cases Pending Field Survey", f"{pending_field_survey}")


def render_breakdown_charts(df):
    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.markdown("**Cases by Type**")
        if len(df) == 0:
            st.caption("No case data yet.")
        else:
            type_counts = df["case_type"].replace("", "Unspecified").value_counts()
            st.bar_chart(type_counts)
            st.dataframe(
                type_counts.rename_axis("Case Type").reset_index(name="Count"),
                use_container_width=True,
                hide_index=True,
            )

    with chart_col2:
        st.markdown("**Cases by Status**")
        if len(df) == 0:
            st.caption("No case data yet.")
        else:
            status_counts = df["status"].replace("", "Unspecified").value_counts()
            st.bar_chart(status_counts)
            st.dataframe(
                status_counts.rename_axis("Status").reset_index(name="Count"),
                use_container_width=True,
                hide_index=True,
            )


def render_filter_panel(df, officer_names):
    st.markdown("### 🔎 Filters")

    all_officers = sorted(set(officer_names) | set(o for o in df["assigned_officer"].unique() if o))

    f_col1, f_col2, f_col3, f_col4, f_col5 = st.columns([1, 1, 1, 1, 1])

    with f_col1:
        case_type_filter = st.selectbox("Case Type", ["All"] + CASE_TYPES, key="filter_case_type")
    with f_col2:
        status_filter = st.selectbox("Status", ["All"] + STATUS_OPTIONS, key="filter_status")
    with f_col3:
        officer_filter = st.selectbox("Assigned Officer", ["All"] + all_officers, key="filter_officer")

    valid_dates = [d for d in df["created_date"].tolist() if d is not None]
    default_start = min(valid_dates) if valid_dates else date.today()
    default_end = max(valid_dates) if valid_dates else date.today()

    with f_col4:
        start_date = st.date_input("From", value=default_start, key="filter_start_date")
    with f_col5:
        end_date = st.date_input("To", value=default_end, key="filter_end_date")

    filtered_df = df.copy()

    if case_type_filter != "All":
        filtered_df = filtered_df[filtered_df["case_type"] == case_type_filter]
    if status_filter != "All":
        filtered_df = filtered_df[filtered_df["status"] == status_filter]
    if officer_filter != "All":
        filtered_df = filtered_df[filtered_df["assigned_officer"] == officer_filter]

    if start_date and end_date:
        def in_range(d):
            if d is None:
                return False
            return start_date <= d <= end_date

        filtered_df = filtered_df[filtered_df["created_date"].apply(in_range)]

    return filtered_df


def render_case_grid(filtered_df):
    st.markdown("### 📋 Case Grid")

    if len(filtered_df) == 0:
        st.info("No cases match the current filters.")
        return filtered_df

    display_df = filtered_df.rename(
        columns={
            "case_id": "Case ID",
            "title": "Title",
            "case_type": "Case Type",
            "status": "Status",
            "location": "Location",
            "created_at": "Date Created",
            "assigned_officer": "Assigned Officer",
        }
    )[
        [
            "Case ID",
            "Title",
            "Case Type",
            "Status",
            "Location",
            "Date Created",
            "Assigned Officer",
        ]
    ]

    st.dataframe(display_df, use_container_width=True, hide_index=True)
    return filtered_df


def render_case_actions(cases_ws, audit_ws, filtered_df, officer_names, user):
    st.markdown("### ⚙️ Case Actions")

    if len(filtered_df) == 0:
        st.caption("Select filters that return at least one case to manage it here.")
        return

    case_options = {
        f"{row['case_id']} — {row['title']}": row["case_id"]
        for _, row in filtered_df.iterrows()
    }
    selected_label = st.selectbox(
        "Select a case to manage", list(case_options.keys()), key="selected_case_label"
    )
    selected_case_id = case_options[selected_label]
    selected_row = filtered_df[filtered_df["case_id"] == selected_case_id].iloc[0]

    st.markdown(f"**Current status:** `{selected_row['status']}`  ·  "
                f"**Current officer:** `{selected_row['assigned_officer'] or '— unassigned —'}`")

    # ---- Document Checklist Grid for the selected case -----------------------
    render_document_checklist_grid(selected_row.to_dict(), key_prefix=selected_case_id)

    action_col1, action_col2 = st.columns(2)

    # ---- Assign officer -----------------------------------------------------
    with action_col1:
        st.markdown("#### Assign Officer")
        officer_choice_options = officer_names if officer_names else [selected_row["assigned_officer"] or ""]
        officer_choice = st.selectbox(
            "Officer", officer_choice_options, key=f"assign_officer_{selected_case_id}"
        )
        if st.button("Assign Officer", key=f"assign_btn_{selected_case_id}", use_container_width=True):
            if not officer_choice:
                st.error("Please choose an officer to assign.")
            else:
                try:
                    update_case_fields(
                        cases_ws,
                        selected_case_id,
                        {"assigned_officer": officer_choice, "status": "Survey Pending"},
                    )
                    append_audit_entry(
                        audit_ws,
                        case_id=selected_case_id,
                        user_name=user["name"],
                        user_role=user["role"],
                        department=user["department"],
                        action=f"Officer assigned: {officer_choice} (status -> Survey Pending)",
                    )
                    st.success(f"Officer '{officer_choice}' assigned to {selected_case_id}.")
                    st.cache_data.clear()
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    # ---- Close case (with mandatory validation) ------------------------------
    with action_col2:
        st.markdown("#### Close Case")

        if str(selected_row["status"]) == "Closed":
            st.info("This case is already closed.")
        else:
            resolution_brief = st.text_area(
                "Resolution brief (what was demolished/cleared)",
                key=f"resolution_brief_{selected_case_id}",
                height=100,
            )
            land_saved_value = st.number_input(
                "Land saved value (area, decimal, > 0)",
                min_value=0.0,
                step=0.1,
                format="%.2f",
                key=f"land_saved_value_{selected_case_id}",
            )
            land_type = st.selectbox(
                "Land type",
                [CLOSE_CASE_LAND_TYPE_PLACEHOLDER] + CLOSE_CASE_LAND_TYPES,
                key=f"land_type_{selected_case_id}",
            )

            is_valid = (
                bool(resolution_brief.strip())
                and land_saved_value > 0
                and land_type in CLOSE_CASE_LAND_TYPES
            )

            missing = []
            if not resolution_brief.strip():
                missing.append("resolution brief")
            if not (land_saved_value > 0):
                missing.append("land saved value greater than 0")
            if land_type not in CLOSE_CASE_LAND_TYPES:
                missing.append("land type")

            if not is_valid:
                st.caption(
                    "⚠️ Before you can close this case, please provide: "
                    + ", ".join(missing) + "."
                )

            if st.button(
                "Close Case",
                key=f"close_btn_{selected_case_id}",
                use_container_width=True,
                disabled=not is_valid,
            ):
                if not is_valid:
                    st.error(
                        "Cannot close case: resolution brief, a land saved value "
                        "greater than 0, and a land type are all required."
                    )
                else:
                    try:
                        update_case_fields(
                            cases_ws,
                            selected_case_id,
                            {
                                "status": "Closed",
                                "resolution_brief": resolution_brief.strip(),
                                "land_saved_value": land_saved_value,
                                "land_type": land_type,
                            },
                        )
                        append_audit_entry(
                            audit_ws,
                            case_id=selected_case_id,
                            user_name=user["name"],
                            user_role=user["role"],
                            department=user["department"],
                            action=(
                                f"Case closed (land_saved_value={land_saved_value}, "
                                f"land_type={land_type})"
                            ),
                        )
                        st.success(f"Case {selected_case_id} closed.")
                        st.cache_data.clear()
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))


def render_head_analytics_board(cases_ws, audit_ws, whitelist_ws, user):
    st.subheader("📊 Performance & Impact Analytics Board")

    cases = load_cases(cases_ws)
    df = cases_to_dataframe(cases)

    render_kpi_cards(df)
    st.divider()
    render_breakdown_charts(df)
    st.divider()

    officer_names = get_operator_names(whitelist_ws)
    filtered_df = render_filter_panel(df, officer_names)
    render_case_grid(filtered_df)
    st.divider()
    render_case_actions(cases_ws, audit_ws, filtered_df, officer_names, user)


# --------------------------------------------------------------------------------
# DASHBOARD
# --------------------------------------------------------------------------------

def render_my_cases(cases_ws, user):
    st.subheader("My Cases")

    cases = load_cases(cases_ws)
    visible_cases = [c for c in cases if c.get("assigned_officer") == user["name"]]

    if not visible_cases:
        st.info("No cases to display yet.")
        return

    st.dataframe(visible_cases, use_container_width=True, hide_index=True)

    st.markdown("#### Document status for a case")
    case_options = {f"{c['case_id']} — {c.get('title', '')}": c for c in visible_cases}
    selected_label = st.selectbox("Select a case", list(case_options.keys()), key="my_cases_case_select")
    render_document_checklist_grid(case_options[selected_label])


def render_dashboard(cases_ws, audit_ws, whitelist_ws, unassigned_ws, drive_service, model, user):
    st.title(f"🛡️ {APP_TITLE}")
    st.caption(f"Signed in as **{user['name']}** ({user['role']}, {user['department']})")

    if user["role"] == "Head":
        tab_analytics, tab_notices, tab_pending_review = st.tabs(
            ["📊 Analytics Board", "📝 Notice Generator", "📥 Pending Review"]
        )
        with tab_analytics:
            render_head_analytics_board(cases_ws, audit_ws, whitelist_ws, user)
        with tab_notices:
            render_notice_generator(cases_ws, audit_ws, drive_service, model, user)
        with tab_pending_review:
            render_pending_review_queue(cases_ws, audit_ws, unassigned_ws, drive_service, model, user)
        return

    # Operator view
    tab_my_cases, tab_bulk_upload, tab_pending_review = st.tabs(
        ["🗂️ My Cases", "📤 Bulk Upload", "📥 Pending Review"]
    )
    with tab_my_cases:
        render_my_cases(cases_ws, user)
    with tab_bulk_upload:
        render_bulk_upload_auto_sorter(cases_ws, audit_ws, unassigned_ws, drive_service, model, user)
    with tab_pending_review:
        render_pending_review_queue(cases_ws, audit_ws, unassigned_ws, drive_service, model, user)


# --------------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------------

def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🛡️", layout="wide")

    whitelist_ws, cases_ws, audit_ws, unassigned_ws = init_sheets()

    if not st.session_state.get("authenticated"):
        render_login_screen(whitelist_ws)
        return

    user = st.session_state.user
    drive_service = get_drive_service()  # may be None — every caller below handles that
    model = get_gemini_model()  # may be None — every AI-briefing/drafting call below handles that

    # Log the login event once per session.
    if not st.session_state.get("login_logged"):
        append_audit_entry(
            audit_ws,
            case_id="",
            user_name=user["name"],
            user_role=user["role"],
            department=user["department"],
            action="Login",
        )
        st.session_state.login_logged = True

    with st.sidebar:
        st.markdown(f"**{user['name']}**  \n{user['email']}")
        st.markdown(f"Role: `{user['role']}` · Dept: `{user['department']}`")
        if model is None:
            st.caption(
                "🤖 AI briefing/drafting: not configured (add `[gemini] api_key` to "
                "secrets.toml) — cases still save normally with a manual/default summary."
            )
        else:
            st.caption(
                "🤖 AI briefing/drafting: enabled (Gemini 1.5 Flash) — falls back "
                "automatically to a manual/default summary if a call ever fails."
            )
        st.caption("📤 File uploads: via Apps Script uploader (no service account needed)")
        if drive_service is None:
            st.caption("📁 Legacy Drive service account: not connected (only affects Pending Review moves)")
        else:
            st.caption("📁 Legacy Drive service account: connected")
        if st.button("Log out", use_container_width=True):
            logout()
        st.divider()

    if user["role"] == "Operator":
        render_new_case_form(cases_ws, audit_ws, drive_service, model, user)

    render_dashboard(cases_ws, audit_ws, whitelist_ws, unassigned_ws, drive_service, model, user)


# --------------------------------------------------------------------------------
# STANDALONE LOCAL UTILITY — PDF SPLITTER
# --------------------------------------------------------------------------------
# This section has NO dependency on Streamlit, gspread, or the Google Drive
# API. It is a plain, importable Python function (plus a small CLI wrapper)
# for locally batch-splitting one giant combined scanned PDF (e.g. a whole
# day's worth of paper filings scanned back-to-back with a blank separator
# page between each document) into separate per-document PDF files.
#
# Usage as a library:
#     from app import split_pdf_on_blank_pages
#     output_paths = split_pdf_on_blank_pages("giant_scan.pdf", "output_dir")
#
# Usage from the command line (bypasses Streamlit entirely):
#     python app.py --split-pdf /path/to/giant_scan.pdf /path/to/output_dir
# --------------------------------------------------------------------------------

def _pdf_page_is_blank_separator(page, text_threshold=15, image_pixel_threshold=40000):
    """Heuristic: a page counts as a blank separator page if it has almost no
    extractable text AND does not contain any embedded image large enough to
    plausibly be a scanned page of content. This deliberately avoids a hard
    dependency on rasterization libraries (e.g. pdf2image + Pillow) so the
    utility works with just `pypdf` installed; if those libraries are
    available in your environment, swap in a whiteness-ratio check on the
    rendered page image for more robust detection of blank-but-imaged pages.
    """
    try:
        text = (page.extract_text() or "").strip()
    except Exception:  # noqa: BLE001
        text = ""
    if len(text) >= text_threshold:
        return False

    try:
        resources = page.get("/Resources")
        if resources:
            xobjects = resources.get("/XObject")
            if xobjects:
                for _, xobj_ref in xobjects.items():
                    xobj = xobj_ref.get_object()
                    if xobj.get("/Subtype") == "/Image":
                        width = int(xobj.get("/Width", 0) or 0)
                        height = int(xobj.get("/Height", 0) or 0)
                        if width * height > image_pixel_threshold:
                            return False  # Has a substantial image: not blank.
    except Exception:  # noqa: BLE001
        # If resource inspection fails for any reason, fall back to the text
        # check above rather than guessing; err on the side of NOT treating
        # an unreadable page as a separator, so content is never dropped.
        return len(text) < text_threshold

    return True


def split_pdf_on_blank_pages(input_pdf_path, output_dir, text_threshold=15, min_pages_per_doc=1):
    """
    Standalone utility (no Streamlit/Sheets/Drive dependency) for local batch
    processing of one giant combined scanned PDF containing multiple
    documents separated by blank pages.

    Args:
        input_pdf_path: path to the combined PDF on local disk.
        output_dir: local folder to write the split-out PDFs into (created
            if it doesn't exist).
        text_threshold: a page with fewer than this many extractable
            characters (and no substantial embedded image) is treated as a
            blank separator page.
        min_pages_per_doc: segments shorter than this many pages are
            discarded (guards against stray single blank-ish pages being
            miscounted as a 1-page "document").

    Returns:
        List of output file paths, one per detected document, in order.

    Raises:
        RuntimeError if `pypdf` is not installed.
        FileNotFoundError if `input_pdf_path` does not exist.
    """
    if not PYPDF_AVAILABLE:
        raise RuntimeError(
            "The 'pypdf' package is required for split_pdf_on_blank_pages() but is not installed. "
            "Install it with: pip install pypdf"
        )
    if not os.path.isfile(input_pdf_path):
        raise FileNotFoundError(f"Input PDF not found: {input_pdf_path}")

    os.makedirs(output_dir, exist_ok=True)
    reader = PypdfReader(input_pdf_path)

    segments = []
    current_segment = []
    for page in reader.pages:
        if _pdf_page_is_blank_separator(page, text_threshold=text_threshold):
            if current_segment:
                segments.append(current_segment)
                current_segment = []
            # Blank separator pages themselves are dropped, not carried into
            # either neighboring document.
        else:
            current_segment.append(page)
    if current_segment:
        segments.append(current_segment)

    output_paths = []
    base_name = os.path.splitext(os.path.basename(input_pdf_path))[0]
    for idx, segment_pages in enumerate(segments, start=1):
        if len(segment_pages) < min_pages_per_doc:
            continue
        writer = PypdfWriter()
        for p in segment_pages:
            writer.add_page(p)
        out_path = os.path.join(output_dir, f"{base_name}_split_{idx:03d}.pdf")
        with open(out_path, "wb") as f:
            writer.write(f)
        output_paths.append(out_path)

    return output_paths


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 2 and sys.argv[1] == "--split-pdf":
        if len(sys.argv) < 3:
            print("Usage: python app.py --split-pdf <input.pdf> [output_dir]")
            sys.exit(1)
        _input_path = sys.argv[2]
        _output_dir = sys.argv[3] if len(sys.argv) > 3 else "split_output"
        try:
            _result_paths = split_pdf_on_blank_pages(_input_path, _output_dir)
        except Exception as _exc:  # noqa: BLE001
            print(f"Error: {_exc}")
            sys.exit(1)
        print(f"Split '{_input_path}' into {len(_result_paths)} document(s) in '{_output_dir}':")
        for _p in _result_paths:
            print(f"  {_p}")
    else:
        main()