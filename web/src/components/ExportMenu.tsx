/**
 * ⬇️ Download the offers list (docs/adr/0009): only customers who said YES to offers.
 * CSV opens in Excel and Google Sheets (File → Import); the Google Ads file uses Customer Match columns.
 */
import { useState } from "react";
import { api, ApiProblem, unwrap } from "../api/client";
import type { paths } from "../api/schema";
import { useToast } from "./common";

// The options come from the contract, so a new format in openapi.yaml shows up here as a type error.
type ExportQuery = NonNullable<paths["/v1/exports/customers"]["get"]["parameters"]["query"]>;
type Choice = { label: string; hint: string; format: NonNullable<ExportQuery["format"]>; layout: NonNullable<ExportQuery["layout"]> };

const CHOICES: Choice[] = [
  { label: "📗 Excel (.xlsx)", hint: "Open in Excel", format: "xlsx", layout: "standard" },
  { label: "📄 CSV", hint: "Google Sheets, Numbers, anything", format: "csv", layout: "standard" },
  { label: "🎯 Google Ads", hint: "Customer Match upload (CSV)", format: "csv", layout: "google-ads" },
];

function filenameFrom(disposition: string | null, fallback: string): string {
  return /filename="([^"]+)"/.exec(disposition ?? "")?.[1] ?? fallback;
}

export function ExportMenu() {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  async function download(choice: Choice) {
    setBusy(true);
    try {
      const result = await api.GET("/v1/exports/customers", {
        params: { query: { format: choice.format, layout: choice.layout } },
        parseAs: "blob",
      });
      const url = URL.createObjectURL(unwrap(result));
      const { response } = result;
      const link = document.createElement("a");
      link.href = url;
      link.download = filenameFrom(response.headers.get("Content-Disposition"), `kiakia-offers-list.${choice.format}`);
      link.click();
      URL.revokeObjectURL(url);
      toast("⬇️ Downloaded. It only lists customers who said YES to offers.");
      setOpen(false);
    } catch (err) {
      toast(err instanceof ApiProblem ? `Couldn't export: ${err.message}` : "Couldn't reach Kiakia. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="export">
      <button className="btn" aria-expanded={open} onClick={() => setOpen(!open)} disabled={busy}>
        ⬇️ Export offers list
      </button>
      {open && (
        <div className="export-menu" role="group" aria-label="Export format">
          <p className="hint">Only customers who said <b>YES to offers</b>. Each export is logged.</p>
          {CHOICES.map((choice) => (
            <button key={choice.label} className="btn" disabled={busy} onClick={() => void download(choice)}>
              {choice.label} <span className="hint">{choice.hint}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
