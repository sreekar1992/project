# Kubernetes deployment

This folder packages the existing workbench as a **private, single-replica
inference service**. It does not make the workbench clinically deployable, and
it intentionally does not bake MLII recordings, checkpoints, encrypted batches,
keys, or the public demo password into the image.

## Recommended AI path

Use the raw MAT waveform as the primary model input. The current pipeline reads
the 3,600-sample MLII signal, filters/normalizes it, and supplies a 900-sample
one-dimensional input to `RAMNV2`. The rendered PNGs are visualization outputs;
fine-tuning a vision model on them is a secondary image-only ablation, not the
recommended classifier.

For a new patient-level dataset, the best next experiment is a compatible
pretrained one-dimensional ECG encoder with a new rhythm-classification head,
then staged backbone unfreezing. With the present 1,000 fragments, do **not**
fully fine-tune a large ViT or generic image model. The committed `RAMNV2`
checkpoint is a small, task-trained baseline rather than a general pretrained
foundation encoder, so the included Kubernetes Job is honestly named
`retrain-baseline-gpu.yaml`.

Before reporting a fine-tuned model, deduplicate samples and use a
patient-/recording-disjoint split. The existing fragment-level split has
recording overlap and is not a clinical performance estimate. Use class-aware
loss/sampling, ECG-safe augmentation, macro-F1 and calibration metrics, early
stopping, and a locked external or patient-level test set.

## Build a local Rancher Desktop image

From the repository root:

```sh
docker build --platform linux/arm64 -t ecg-workbench:dev .
kubectl apply -k k8s/base
```

The application is deliberately exposed only by a `ClusterIP` Service. After the
model/data PVC has been populated and the Pod is Ready, access it locally:

```sh
kubectl -n ecg-research port-forward service/ecg-workbench 8765:8765
```

Then open `http://127.0.0.1:8765`.

## Supply data without putting it in the image

`k8s/base/pvc.yaml` creates the `ecg-workbench-data` PVC. Populate it with this
layout before expecting readiness to pass:

```text
/data/
  MLII/                  # optional for inference; required for retraining
  artifacts_final/
    model.pt             # required by the default checkpoint selector
    metrics.json
    label_map.json
  artifacts_secure/      # created by the app if its secure endpoints are used
  artifacts_dataset/     # only if you deliberately run batch research jobs
```

The readiness probe returns `503` until at least one `artifacts*/model.pt` file
is mounted. The runtime PVC must be writable by UID/GID `10001`. Do not use
`hostPath` or place real patient information in a public image repository.

## Training and batch jobs

The web Deployment disables browser-triggered training, robustness, and
whole-dataset encryption jobs. They retain in-memory job state and are unsafe to
scale across replicas. Run long operations as dedicated Kubernetes Jobs instead.

`jobs/retrain-baseline-gpu.yaml` is an optional CUDA example and requires a
configured NVIDIA device plugin plus a `nvidia.com/gpu` resource. Change its
image tag and output directory for every experiment; never overwrite a validated
checkpoint. For CPU-only retraining, remove the GPU limit and use `--device cpu`.

The existing batch-encryption workflow is demo-only because its password is
public. Do not expose its endpoints or deploy it for real patient data. A
production system needs managed secrets, per-user authentication/authorization,
audit logging, encrypted object storage, and a clinical/privacy review.

## Publishing an image

For a registry image, replace `ecg-workbench:dev` in the Deployment and Job with
an immutable digest or a version tag, then apply the base again. Keep the
Service private until you configure an authenticated TLS Ingress and add its
hostname to `ECG_TRUSTED_HOSTS` in `base/configmap.yaml`.

Kubernetes probes are used to distinguish process startup/liveness from model
readiness. GPU requests require a vendor device plugin. Refer to the official
Kubernetes documentation for [probes](https://kubernetes.io/docs/concepts/workloads/pods/probes/)
and [GPU scheduling](https://kubernetes.io/docs/tasks/manage-gpus/scheduling-gpus/).
