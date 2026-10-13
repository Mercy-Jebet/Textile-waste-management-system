class Bin:
    bin_id = str
    location = str
    height_cm = float
    fill_level= float
    status = str

    def __init__(self, bin_id, location, height_cm, fill_level, status):
        self.bin_id = bin_id
        self.location = location
        self.height_cm = height_cm
        self.fill_level = fill_level
        self.status = status

    def detect_fill_level(self):
        # Logic to detect the fill level of the bin
        pass

    def update_status(self):
        # Logic to update the status of the bin based on the fill level
        pass

    def transmit_to_firebase(self):
        # Logic to transmit the bin data to Firebase
        pass

    def trigger_alert(self):
        # Logic to trigger an alert based on the bin status
        pass



class Collector:
    collector_id = str()
    name = str()
    email = str()
    phone_number = str()
    area = str()

    def __init__(self, collector_id, name, email, phone_number, area):
        self.collector_id = collector_id
        self.name = name
        self.email = email
        self.phone_number = phone_number
        self.area = area


    def view_dashboard(self):
        # Logic to view the dashboard for the collector
        pass

    def receive_alert(self):
        # Logic to receive an alert for the collector
        pass

    def acknowledge_alert(self):
        # Logic to acknowledge an alert for the collector
        pass


from datetime import time


class Dashboard:
    bin_data= dict()
    alert_threshold = float()
    collector_email= str()
    last_refresh= time()

    def __init__(self, bin_data, alert_threshold, collector_email, last_refresh):
        self.bin_data = bin_data
        self.alert_threshold = alert_threshold
        self.collector_email = collector_email
        self.last_refresh = last_refresh


    def display_fill_levels(self):
        # Logic to display the fill levels of all bins
        for bin_id, fill_level in self.bin_data.items():
            print(f"Bin ID: {bin_id}, Fill Level: {fill_level}")

    def auto_update(self):
        # Logic to automatically update the dashboard with the latest bin data
        pass

    def send_email_alert(self):
        # Logic to send an email alert to the collector
        pass

    def render_history(self):
        # Logic to render the historical data of bin fill levels
        pass

    def render_rewards_page(self):
        # Logic to render the rewards page for users based on their contributions
        pass

from datetime import time


class Deposit:
    deposit_id= str()
    consumer_id = str()
    bin_id = str()
    deposit_timestamp = time()
    points_earned = int()

    def __init__(self, deposit_id, consumer_id, bin_id, deposit_timestamp, points_earned):
        self.deposit_id = deposit_id
        self.consumer_id = consumer_id
        self.bin_id = bin_id
        self.deposit_timestamp = deposit_timestamp
        self.points_earned = points_earned

    def record_deposit(self):
        # Logic to record the deposit in the system
        pass

    def assign_points(self):
        # Logic to assign points to the consumer for the deposit
        pass

    def get_history(self):
        # Logic to retrieve the deposit history for the consumer
        pass



class Sensor:
    sensor_id = str
    bin_id = str
    trig_pin = int
    echo_pin = int
    last_reading = float

    def __init__(self,sensor_id, bin_id, trig_pin, echo_pin,last_reading):
        self.sensor_id = sensor_id 
        self.bin_id = bin_id
        self.trig_pin = trig_pin
        self.echo_pin = echo_pin
        self.last_reading = last_reading

    def trigger_pulse(self):
        # Logic to trigger the sensor pulse
        pass

    def measure_distance(self):
        # Logic to measure the distance using the sensor
        pass

    def get_fill_level(self):
        # Logic to calculate the fill level based on the last reading
        pass

class Consumer:
    consumer_id = str()
    name = str()
    email = str()
    points_balance = int()

    def __init__(self, consumer_id, name, email, points_balance):
        self.consumer_id = consumer_id
        self.name = name
        self.email = email
        self.points_balance = points_balance

    def update_points(self, points):
        # Logic to update the consumer's points balance
        self.points_balance += points

    def get_deposit_history(self):
        # Logic to retrieve the consumer's deposit history
        pass