import AdminPanelSettingsOutlinedIcon from "@mui/icons-material/AdminPanelSettingsOutlined";
import LogoutOutlinedIcon from "@mui/icons-material/LogoutOutlined";
import MonitorHeartOutlinedIcon from "@mui/icons-material/MonitorHeartOutlined";
import PeopleAltOutlinedIcon from "@mui/icons-material/PeopleAltOutlined";
import SpaceDashboardOutlinedIcon from "@mui/icons-material/SpaceDashboardOutlined";
import {
  AppBar,
  Box,
  Button,
  Chip,
  Container,
  Divider,
  Drawer,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Stack,
  Toolbar,
  Typography,
} from "@mui/material";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { ClinicalSafetyBanner } from "./ClinicalSafetyBanner";

const drawerWidth = 244;

const clinicalNavigation = [
  { label: "Dashboard", to: "/", icon: <SpaceDashboardOutlinedIcon /> },
  { label: "Patients", to: "/patients", icon: <PeopleAltOutlinedIcon /> },
];

export function AppShell() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  const isSuperAdmin = user?.roles?.includes("SUPER_ADMIN") ?? false;
  const navigation = isSuperAdmin
    ? [{ label: "Super Admin", to: "/super-admin", icon: <AdminPanelSettingsOutlinedIcon /> }]
    : clinicalNavigation;

  async function handleSignOut() {
    await signOut();
    navigate("/login", { replace: true });
  }

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "background.default" }}>
      <AppBar position="fixed" color="inherit" elevation={0} sx={{ borderBottom: "1px solid #dce7e3", zIndex: (theme) => theme.zIndex.drawer + 1 }}>
        <Toolbar sx={{ gap: 1.5 }}>
          <MonitorHeartOutlinedIcon color="primary" />
          <Typography component="div" variant="h6" sx={{ flexGrow: 1, fontWeight: 800 }}>
            ECG Research Clinical Platform
          </Typography>
          <Chip size="small" label={isSuperAdmin ? "Research platform controls" : "Clinician review required"} color="warning" variant="outlined" />
          <Typography variant="body2" color="text.secondary" sx={{ display: { xs: "none", md: "block" } }}>
            {user?.display_name ?? user?.name ?? user?.email ?? user?.role ?? "Authenticated user"}
          </Typography>
          <Button color="inherit" startIcon={<LogoutOutlinedIcon />} onClick={handleSignOut}>
            Sign out
          </Button>
        </Toolbar>
      </AppBar>

      <Drawer
        variant="permanent"
        sx={{ width: drawerWidth, flexShrink: 0, "& .MuiDrawer-paper": { width: drawerWidth, boxSizing: "border-box", pt: 8, borderRight: "1px solid #dce7e3" } }}
      >
        <List sx={{ px: 1, py: 2 }}>
          {navigation.map((item) => (
            <ListItemButton
              component={NavLink}
              to={item.to}
              key={item.to}
              end={item.to === "/"}
              sx={{ mb: 0.5, borderRadius: 2, "&.active": { bgcolor: "primary.main", color: "primary.contrastText", "& .MuiListItemIcon-root": { color: "primary.contrastText" } } }}
            >
              <ListItemIcon>{item.icon}</ListItemIcon>
              <ListItemText primary={item.label} />
            </ListItemButton>
          ))}
        </List>
        <Box sx={{ mt: "auto", p: 2 }}>
          <Divider sx={{ mb: 2 }} />
          <Typography variant="caption" color="text.secondary">
            Data visibility and actions are governed by the API role and hospital tenant.
          </Typography>
        </Box>
      </Drawer>

      <Box component="main" sx={{ ml: `${drawerWidth}px`, pt: 10, pb: 5 }}>
        <Container maxWidth="xl">
          <Stack spacing={3}>
            <ClinicalSafetyBanner compact />
            <Outlet />
          </Stack>
        </Container>
      </Box>
    </Box>
  );
}
