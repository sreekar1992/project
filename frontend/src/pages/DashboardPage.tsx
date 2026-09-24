import AssessmentOutlinedIcon from "@mui/icons-material/AssessmentOutlined";
import FactCheckOutlinedIcon from "@mui/icons-material/FactCheckOutlined";
import GroupsOutlinedIcon from "@mui/icons-material/GroupsOutlined";
import MonitorHeartOutlinedIcon from "@mui/icons-material/MonitorHeartOutlined";
import { Box, Card, CardContent, List, ListItem, ListItemText, Paper, Stack, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { ApiErrorAlert } from "../components/ApiErrorAlert";
import { LoadingState } from "../components/LoadingState";
import { api } from "../lib/api";
import type { DashboardResponse } from "../types/api";

function labelFor(value: string): string {
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function metricEntries(payload: DashboardResponse): [string, string | number][] {
  const candidate = payload.metrics ?? Object.fromEntries(
    Object.entries(payload).filter(([, value]) => typeof value === "number" || typeof value === "string"),
  );
  return Object.entries(candidate).filter(([, value]) => typeof value === "number" || typeof value === "string") as [string, string | number][];
}

const icons = [<GroupsOutlinedIcon key="patients" />, <MonitorHeartOutlinedIcon key="ecgs" />, <AssessmentOutlinedIcon key="analyses" />, <FactCheckOutlinedIcon key="reviews" />];

export function DashboardPage() {
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard });

  if (dashboard.isLoading) return <LoadingState label="Loading dashboard data…" />;
  if (dashboard.isError) return <ApiErrorAlert error={dashboard.error} />;

  const metrics = metricEntries(dashboard.data ?? {});
  const activity = dashboard.data?.recent_activity ?? [];

  return (
    <Stack spacing={3}>
      <Box>
        <Typography variant="overline" color="primary.main" sx={{ fontWeight: 800, letterSpacing: 1.6 }}>
          Hospital research workspace
        </Typography>
        <Typography variant="h4">Clinical review dashboard</Typography>
        <Typography color="text.secondary" sx={{ mt: 0.75 }}>
          Operational counts are loaded from your authorized hospital tenant. Predictions remain pending until clinician review is recorded.
        </Typography>
      </Box>

      {metrics.length > 0 ? (
        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))", lg: "repeat(4, minmax(0, 1fr))" }, gap: 2 }}>
          {metrics.map(([key, value], index) => (
            <Box key={key}>
              <Card>
                <CardContent>
                  <Stack direction="row" alignItems="center" spacing={1.5}>
                    <Box sx={{ p: 1, borderRadius: 2, bgcolor: "rgba(11, 93, 76, 0.08)", color: "primary.main", display: "grid", placeItems: "center" }}>
                      {icons[index % icons.length]}
                    </Box>
                    <Box>
                      <Typography variant="h5">{String(value)}</Typography>
                      <Typography variant="body2" color="text.secondary">{labelFor(key)}</Typography>
                    </Box>
                  </Stack>
                </CardContent>
              </Card>
            </Box>
          ))}
        </Box>
      ) : (
        <Paper sx={{ p: 3 }}>
          <Typography variant="h6">No dashboard metrics were returned</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            Once the `/api/v1/dashboard` endpoint is connected, its authorized metrics appear here.
          </Typography>
        </Paper>
      )}

      <Paper sx={{ p: 0 }}>
        <Box sx={{ px: 3, py: 2, borderBottom: "1px solid #dce7e3" }}>
          <Typography variant="h6">Recent authorized activity</Typography>
          <Typography variant="body2" color="text.secondary">Audit information is supplied by the backend; no activity is fabricated in this view.</Typography>
        </Box>
        {activity.length ? (
          <List disablePadding>
            {activity.map((item, index) => (
              <ListItem divider={index < activity.length - 1} key={item.id ?? `${item.created_at ?? "activity"}-${index}`}>
                <ListItemText
                  primary={item.description ?? item.action ?? "Recorded activity"}
                  secondary={[item.actor_name, item.created_at].filter(Boolean).join(" · ") || undefined}
                />
              </ListItem>
            ))}
          </List>
        ) : (
          <Box sx={{ p: 3 }}><Typography color="text.secondary">No authorized activity was returned.</Typography></Box>
        )}
      </Paper>
    </Stack>
  );
}
