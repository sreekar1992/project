import { Alert } from "@mui/material";
import { apiErrorMessage } from "../lib/api";

export function ApiErrorAlert({ error }: { error: unknown }) {
  if (!error) return null;
  return <Alert severity="error">{apiErrorMessage(error)}</Alert>;
}
