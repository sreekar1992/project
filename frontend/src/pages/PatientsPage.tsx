import AddOutlinedIcon from "@mui/icons-material/AddOutlined";
import SearchOutlinedIcon from "@mui/icons-material/SearchOutlined";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  InputAdornment,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";
import { z } from "zod";
import { ApiErrorAlert } from "../components/ApiErrorAlert";
import { LoadingState } from "../components/LoadingState";
import { api } from "../lib/api";
import { listFromResponse, patientDisplayName } from "../types/api";

const patientSchema = z.object({
  mrn: z.string().trim().min(1, "MRN is required.").max(100),
  first_name: z.string().trim().min(1, "First name is required.").max(100),
  last_name: z.string().trim().min(1, "Last name is required.").max(100),
  date_of_birth: z.string().min(1, "Date of birth is required."),
  sex_at_birth: z.string().optional(),
  phone: z.string().trim().max(50).optional(),
  email: z.string().trim().email("Enter a valid email address.").or(z.literal("")),
});

type PatientValues = z.infer<typeof patientSchema>;

function formatDate(value?: string): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleDateString();
}

export function PatientsPage() {
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const patients = useQuery({
    queryKey: ["patients", query],
    queryFn: () => {
      const parameters = new URLSearchParams();
      if (query) parameters.set("search", query);
      return api.patients.list(parameters);
    },
  });
  const form = useForm<PatientValues>({
    resolver: zodResolver(patientSchema),
    defaultValues: { mrn: "", first_name: "", last_name: "", date_of_birth: "", sex_at_birth: "", phone: "", email: "" },
  });
  const createPatient = useMutation({
    mutationFn: (values: PatientValues) => api.patients.create({ ...values, email: values.email || undefined }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["patients"] });
      form.reset();
      setOpen(false);
    },
  });

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  if (patients.isLoading) return <LoadingState label="Loading authorized patient records…" />;
  if (patients.isError) return <ApiErrorAlert error={patients.error} />;

  const items = listFromResponse(patients.data ?? []);

  return (
    <Stack spacing={3}>
      <Box display="flex" alignItems={{ xs: "flex-start", sm: "center" }} justifyContent="space-between" gap={2} flexDirection={{ xs: "column", sm: "row" }}>
        <Box>
          <Typography variant="h4">Patients</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>Search and register patients within the active hospital tenant.</Typography>
        </Box>
        <Button variant="contained" startIcon={<AddOutlinedIcon />} onClick={() => setOpen(true)}>Register patient</Button>
      </Box>

      <Paper sx={{ overflow: "hidden" }}>
        <Box sx={{ p: 2, borderBottom: "1px solid #dce7e3" }}>
          <TextField
            fullWidth
            label="Search patients"
            placeholder="Search by MRN or name"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            InputProps={{ startAdornment: <InputAdornment position="start"><SearchOutlinedIcon /></InputAdornment> }}
          />
        </Box>
        <TableContainer>
          <Table aria-label="Authorized patient records">
            <TableHead>
              <TableRow>
                <TableCell>Patient</TableCell>
                <TableCell>MRN</TableCell>
                <TableCell>Date of birth</TableCell>
                <TableCell>Sex at birth</TableCell>
                <TableCell>Last updated</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {items.map((patient) => (
                <TableRow key={patient.id} hover component={Link} to={`/patients/${encodeURIComponent(patient.id)}`} sx={{ textDecoration: "none", "& > *": { color: "inherit" } }}>
                  <TableCell><Typography fontWeight={700}>{patientDisplayName(patient)}</Typography></TableCell>
                  <TableCell>{patient.mrn ?? "—"}</TableCell>
                  <TableCell>{formatDate(patient.date_of_birth)}</TableCell>
                  <TableCell>{patient.sex_at_birth ?? "—"}</TableCell>
                  <TableCell>{formatDate(patient.updated_at ?? patient.created_at)}</TableCell>
                </TableRow>
              ))}
              {!items.length && (
                <TableRow><TableCell colSpan={5} align="center" sx={{ py: 5, color: "text.secondary" }}>No authorized patients match this search.</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      <Dialog open={open} onClose={() => !createPatient.isPending && setOpen(false)} fullWidth maxWidth="sm">
        <Box component="form" onSubmit={form.handleSubmit((values) => createPatient.mutate(values))}>
          <DialogTitle>Register patient</DialogTitle>
          <DialogContent>
            <Stack spacing={2} sx={{ pt: 1 }}>
              {createPatient.isError && <ApiErrorAlert error={createPatient.error} />}
              <TextField label="Medical record number (MRN)" required error={Boolean(form.formState.errors.mrn)} helperText={form.formState.errors.mrn?.message} {...form.register("mrn")} />
              <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                <TextField fullWidth label="First name" required error={Boolean(form.formState.errors.first_name)} helperText={form.formState.errors.first_name?.message} {...form.register("first_name")} />
                <TextField fullWidth label="Last name" required error={Boolean(form.formState.errors.last_name)} helperText={form.formState.errors.last_name?.message} {...form.register("last_name")} />
              </Stack>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                <TextField fullWidth required label="Date of birth" type="date" InputLabelProps={{ shrink: true }} error={Boolean(form.formState.errors.date_of_birth)} helperText={form.formState.errors.date_of_birth?.message} {...form.register("date_of_birth")} />
                <TextField fullWidth label="Sex at birth" {...form.register("sex_at_birth")} />
              </Stack>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                <TextField fullWidth label="Phone" {...form.register("phone")} />
                <TextField fullWidth label="Email" type="email" error={Boolean(form.formState.errors.email)} helperText={form.formState.errors.email?.message} {...form.register("email")} />
              </Stack>
              <Typography variant="caption" color="text.secondary">The server must validate identity, consent, tenant scope, and audit this registration.</Typography>
            </Stack>
          </DialogContent>
          <DialogActions sx={{ px: 3, pb: 2 }}>
            <Button onClick={() => setOpen(false)} disabled={createPatient.isPending}>Cancel</Button>
            <Button type="submit" variant="contained" disabled={createPatient.isPending}>{createPatient.isPending ? "Registering…" : "Register patient"}</Button>
          </DialogActions>
        </Box>
      </Dialog>
    </Stack>
  );
}
