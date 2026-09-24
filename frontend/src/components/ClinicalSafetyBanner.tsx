import ScienceOutlinedIcon from "@mui/icons-material/ScienceOutlined";
import { Alert, AlertTitle } from "@mui/material";

export function ClinicalSafetyBanner({ compact = false }: { compact?: boolean }) {
  return (
    <Alert severity="warning" icon={<ScienceOutlinedIcon fontSize="inherit" />} sx={{ alignItems: "center" }}>
      {!compact && <AlertTitle sx={{ fontWeight: 700 }}>Research-only clinical decision support</AlertTitle>}
      AI output is not a diagnosis or treatment recommendation. A qualified clinician must review the waveform, context, and result before any clinical use.
    </Alert>
  );
}
