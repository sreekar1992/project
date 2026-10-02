locals {
  services = toset([
    "artifactregistry.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "redis.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
  ])
  namespace    = "ecg-clinical-research"
  workload_ksa = "ecg-platform"
}

resource "google_project_service" "required" {
  for_each           = local.services
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

resource "google_compute_network" "main" {
  name                    = "${var.name_prefix}-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.required]
}

resource "google_compute_subnetwork" "gke" {
  name          = "${var.name_prefix}-gke"
  region        = var.region
  network       = google_compute_network.main.id
  ip_cidr_range = "10.40.0.0/20"

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.48.0.0/16"
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.41.0.0/20"
  }
  private_ip_google_access = true
}

resource "google_compute_router" "main" {
  name    = "${var.name_prefix}-router"
  region  = var.region
  network = google_compute_network.main.id
}

resource "google_compute_router_nat" "main" {
  name                               = "${var.name_prefix}-nat"
  router                             = google_compute_router.main.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

resource "google_compute_global_address" "services" {
  name          = "${var.name_prefix}-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.main.id
}

resource "google_service_networking_connection" "services" {
  network                 = google_compute_network.main.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.services.name]
  depends_on              = [google_project_service.required]
}

resource "google_service_account" "nodes" {
  account_id   = "ecg-gke-nodes"
  display_name = "ECG GKE node identity"
  depends_on   = [google_project_service.required]
}

resource "google_project_iam_member" "node_default" {
  project = var.project_id
  role    = "roles/container.defaultNodeServiceAccount"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_container_cluster" "main" {
  name                = "${var.name_prefix}-gke"
  location            = var.region
  network             = google_compute_network.main.id
  subnetwork          = google_compute_subnetwork.gke.id
  enable_autopilot    = true
  deletion_protection = true

  release_channel { channel = "REGULAR" }
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }
  cluster_autoscaling {
    auto_provisioning_defaults {
      service_account = google_service_account.nodes.email
    }
  }
  depends_on = [google_project_service.required, google_compute_router_nat.main, google_project_iam_member.node_default]
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "ecg-platform"
  format        = "DOCKER"
  description   = "ECG clinical research container images"
  depends_on    = [google_project_service.required]
}

resource "google_artifact_registry_repository_iam_member" "node_pull" {
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_sql_database_instance" "main" {
  name                = "${var.name_prefix}-postgres"
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = true
  settings {
    tier              = var.database_tier
    availability_type = "ZONAL"
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
    }
    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.main.id
    }
  }
  depends_on = [google_service_networking_connection.services, google_project_service.required]
}

resource "google_sql_database" "app" {
  name     = "ecg_platform"
  instance = google_sql_database_instance.main.name
}

resource "random_password" "database" {
  length  = 40
  special = false
}

resource "google_sql_user" "app" {
  name     = "ecg_app"
  instance = google_sql_database_instance.main.name
  password = random_password.database.result
}

resource "google_secret_manager_secret" "database_password" {
  secret_id = "${var.name_prefix}-database-password"
  replication {
    auto {}
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "database_password" {
  secret      = google_secret_manager_secret.database_password.id
  secret_data = random_password.database.result
}

resource "google_redis_instance" "main" {
  name               = "${var.name_prefix}-redis"
  region             = var.region
  tier               = "BASIC"
  memory_size_gb     = 1
  redis_version      = "REDIS_7_2"
  authorized_network = google_compute_network.main.id
  connect_mode       = "PRIVATE_SERVICE_ACCESS"
  depends_on         = [google_service_networking_connection.services, google_project_service.required]
}

resource "google_storage_bucket" "assets" {
  name                        = "${var.project_id}-${var.name_prefix}-assets"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning { enabled = true }
  depends_on = [google_project_service.required]
}

resource "google_storage_bucket_object" "model" {
  name   = "models/model.pt"
  bucket = google_storage_bucket.assets.name
  source = "${path.module}/../../artifacts_final/model.pt"
}

resource "google_service_account" "workload" {
  account_id   = "ecg-platform-workload"
  display_name = "ECG platform workload identity"
  depends_on   = [google_project_service.required]
}

resource "google_storage_bucket_iam_member" "workload_assets" {
  bucket = google_storage_bucket.assets.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.workload.email}"
}

resource "google_service_account_iam_member" "workload_identity" {
  service_account_id = google_service_account.workload.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${local.namespace}/${local.workload_ksa}]"
}

resource "google_service_account" "deploy" {
  account_id   = "ecg-github-deploy"
  display_name = "GitHub Actions GKE deploy"
  depends_on   = [google_project_service.required]
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "ecg-github"
  display_name              = "ECG GitHub Actions"
  depends_on                = [google_project_service.required]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-main"
  display_name                       = "GitHub main branch"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  attribute_condition = "assertion.repository == '${var.github_repository}' && assertion.ref == 'refs/heads/main'"
  oidc { issuer_uri = "https://token.actions.githubusercontent.com" }
}

resource "google_service_account_iam_member" "github_deploy" {
  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

resource "google_artifact_registry_repository_iam_member" "deploy_push" {
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_project_iam_member" "deploy_cluster" {
  project = var.project_id
  role    = "roles/container.developer"
  member  = "serviceAccount:${google_service_account.deploy.email}"
}
