import easyocr
import cv2

reader = easyocr.Reader(['en'], gpu=False)

def detect_plate(image_path):
    img = cv2.imread(image_path)
    img_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    results = reader.readtext(img_gray)

    for detection in results:
        bbox, text, conf = detection
        # Print results for debugging
        print(f"Detected: {text} | Confidence: {conf}")
        
        if 4 <= len(text) <= 12 and conf > 0.3:
            return text.upper()

    return "UNKNOWN"
