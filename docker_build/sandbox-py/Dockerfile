# Image hop cach ly chay code phan tich du lieu — sandbox-py:v1
# Dung khong mang tu thu muc tai wheel (/tmp/sandbox_khoa_goi):
#   docker build --network none --build-context banh=/tmp/sandbox_khoa_goi -f Dockerfile -t sandbox-py:v1 .
FROM python:3.12-slim@sha256:e695ab189fa2d082b1c61c69a6b888a5adeca61ceeccad107b1ed2a125721a9e

COPY goi_khoa.txt /chay/goi_khoa.txt
RUN --mount=type=bind,from=banh,target=/banh \
    pip install --no-cache-dir --no-index --find-links /banh --require-hashes --only-binary=:all: --no-deps -r /chay/goi_khoa.txt \
 && mkdir -p /code /dau_vao /out \
 && pip freeze > /chay/goi.txt \
 && python -m pip uninstall -y pip \
 && rm -rf /usr/local/lib/python3.12/ensurepip /root/.cache \
 && find / -xdev -type f \( -perm -4000 -o -perm -2000 \) -exec chmod a-s {} +

ENV HOME=/tmp MPLCONFIGDIR=/tmp/mpl MPLBACKEND=Agg PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

USER 65534:65534
WORKDIR /out
