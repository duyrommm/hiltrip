# Gunicorn tự đọc file này khi chạy trong thư mục repo (không cần sửa Start Command trên Render).
# Điện thoại hỏi máy chủ 2 giây một lần; giữ kết nối 10 giây để không đóng đúng lúc điện thoại dùng lại
# (mặc định 2 giây trùng chu kỳ, gây lỗi "Server disconnected" lác đác khi đông người).
keepalive = 10
