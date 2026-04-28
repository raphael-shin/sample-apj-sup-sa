################################################################################
# EFS Module for Shared ML Data Storage
################################################################################
# Provides ReadWriteMany storage for multi-node training workloads
# - EFS file system with elastic throughput
# - Mount targets in each private subnet
# - Security group for NFS access from VPC
# - EFS CSI driver for Kubernetes integration
################################################################################

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID"
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR block for security group rules"
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for EFS mount targets"
  type        = list(string)
}

variable "oidc_provider_arn" {
  description = "OIDC provider ARN for IRSA"
  type        = string
}

variable "tags" {
  description = "Tags to apply to resources"
  type        = map(string)
  default     = {}
}

################################################################################
# EFS File System
################################################################################

resource "aws_efs_file_system" "ml_data" {
  creation_token = "${var.cluster_name}-ml-data"
  encrypted      = true

  # Elastic throughput scales automatically with workload
  throughput_mode = "elastic"

  # Performance mode - generalPurpose is recommended for most workloads
  performance_mode = "generalPurpose"

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-ml-data"
  })
}

################################################################################
# Security Group for EFS
################################################################################

resource "aws_security_group" "efs" {
  name        = "${var.cluster_name}-efs"
  description = "Security group for EFS mount targets"
  vpc_id      = var.vpc_id

  ingress {
    description = "NFS from VPC"
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.cluster_name}-efs"
  })
}

################################################################################
# EFS Mount Targets (one per subnet/AZ)
################################################################################

resource "aws_efs_mount_target" "ml_data" {
  for_each = { for idx, id in var.private_subnet_ids : idx => id }

  file_system_id  = aws_efs_file_system.ml_data.id
  subnet_id       = each.value
  security_groups = [aws_security_group.efs.id]
}

################################################################################
# IRSA for EFS CSI Driver
################################################################################

module "efs_csi_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name             = "${var.cluster_name}-efs-csi"
  attach_efs_csi_policy = true

  oidc_providers = {
    main = {
      provider_arn               = var.oidc_provider_arn
      namespace_service_accounts = ["kube-system:efs-csi-controller-sa"]
    }
  }

  tags = var.tags
}

################################################################################
# EFS CSI Driver
################################################################################

resource "helm_release" "aws_efs_csi_driver" {
  name       = "aws-efs-csi-driver"
  repository = "https://kubernetes-sigs.github.io/aws-efs-csi-driver/"
  chart      = "aws-efs-csi-driver"
  namespace  = "kube-system"
  version    = "3.0.8"

  set {
    name  = "controller.serviceAccount.create"
    value = "true"
  }

  set {
    name  = "controller.serviceAccount.name"
    value = "efs-csi-controller-sa"
  }

  set {
    name  = "controller.serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = module.efs_csi_irsa.iam_role_arn
  }

  # Node service account doesn't need IRSA
  set {
    name  = "node.serviceAccount.create"
    value = "true"
  }

  set {
    name  = "node.serviceAccount.name"
    value = "efs-csi-node-sa"
  }

  depends_on = [aws_efs_mount_target.ml_data]
}

################################################################################
# Kubernetes StorageClass for EFS
################################################################################

resource "kubernetes_storage_class_v1" "efs" {
  metadata {
    name = "efs-sc"
  }

  storage_provisioner = "efs.csi.aws.com"

  parameters = {
    provisioningMode = "efs-ap"
    fileSystemId     = aws_efs_file_system.ml_data.id
    directoryPerms   = "700"
    uid              = "0"
    gid              = "0"
  }

  reclaim_policy      = "Delete"
  volume_binding_mode = "Immediate"

  depends_on = [helm_release.aws_efs_csi_driver]
}

################################################################################
# Outputs
################################################################################

output "file_system_id" {
  description = "EFS file system ID"
  value       = aws_efs_file_system.ml_data.id
}

output "file_system_arn" {
  description = "EFS file system ARN"
  value       = aws_efs_file_system.ml_data.arn
}

output "storage_class_name" {
  description = "Kubernetes StorageClass name for EFS"
  value       = kubernetes_storage_class_v1.efs.metadata[0].name
}

output "security_group_id" {
  description = "EFS security group ID"
  value       = aws_security_group.efs.id
}
