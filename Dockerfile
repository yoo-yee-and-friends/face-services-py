# ใช้ base image เป็น Python 3.9.6
FROM python:3.9.6-slim

# ตั้งค่า working directory ใน container
WORKDIR /app

# ติดตั้ง dependencies พื้นฐานและที่จำเป็นสำหรับ dlib และ OpenCV
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    libboost-all-dev \
    libopenblas-dev \
    liblapack-dev \
    libx11-dev \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# คัดลอกไฟล์ requirements.txt ไปยัง container
COPY requirements.txt .

# Upgrade pip_
RUN python -m pip install --upgrade pip

# ติดตั้ง dependencies จาก requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# คัดลอกโค้ดโปรเจกต์ทั้งหมดไปยัง container
COPY . .

# ระบุ port ที่ container จะใช้งาน (กรณี FastAPI)
EXPOSE 8000

# คำสั่งเริ่มต้นเมื่อ container รัน (ใช้ uvicorn สำหรับ FastAPI)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]