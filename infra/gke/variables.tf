variable "project_id" {
  description = "Existing Google Cloud project with billing enabled."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "Provide a valid Google Cloud project ID."
  }
}

variable "region" {
  description = "Region for GKE and managed services."
  type        = string
  default     = "asia-south1"
}

variable "name_prefix" {
  type    = string
  default = "ecg-research"
}

variable "github_repository" {
  description = "GitHub owner/repository permitted to deploy from main."
  type        = string
  default     = "sreekar1992/project"
}

variable "database_tier" {
  type    = string
  default = "db-custom-1-3840"
}
