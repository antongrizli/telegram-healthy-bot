from datetime import datetime, time, UTC
from sqlalchemy import BigInteger, Column, Integer, Float, String, Boolean, DateTime, Time, ForeignKey, JSON
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    telegram_id = Column(BigInteger, primary_key=True)
    username = Column(String, nullable=True)
    name = Column(String, nullable=False)
    sex = Column(String(10), nullable=False) # 'male', 'female'
    age = Column(Integer, nullable=False)
    height_cm = Column(Float, nullable=False)
    weight_kg = Column(Float, nullable=False)
    activity_level = Column(String(20), nullable=False) # 'sedentary', 'light', 'moderate', 'active'
    goal = Column(String(20), nullable=False) # 'lose_weight', 'maintain', 'gain_weight', 'gain_muscle'
    language = Column(String(5), nullable=False, default="en") # 'en', 'ru'
    timezone = Column(String(50), nullable=False, default="UTC")
    notifications_enabled = Column(Boolean, default=True)
    daily_report_time = Column(Time, default=time(21, 0))
    weekly_report_day = Column(Integer, default=6) # 0 = Monday, 6 = Sunday
    monthly_report_day = Column(Integer, default=1) # Day of the month: 1 to 28
    food_reminder_time = Column(Time, default=time(11, 0))
    target_calories = Column(Integer, nullable=False)
    target_protein = Column(Integer, nullable=False)
    target_fat = Column(Integer, nullable=False)
    target_carb = Column(Integer, nullable=False)
    is_blocked = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    # Gamification
    current_streak = Column(Integer, default=0, nullable=False)
    streak_freezes_left = Column(Integer, default=1, nullable=False)
    last_freeze_used_at = Column(DateTime, nullable=True)

    food_logs = relationship("FoodLog", back_populates="user", cascade="all, delete-orphan")
    weight_logs = relationship("WeightLog", back_populates="user", cascade="all, delete-orphan")
    message_stats = relationship("MessageStat", back_populates="user", cascade="all, delete-orphan")
    ai_queues = relationship("AiRequestQueue", back_populates="user", cascade="all, delete-orphan")
    streaks = relationship("Streak", back_populates="user", cascade="all, delete-orphan")
    achievements = relationship("Achievement", back_populates="user", cascade="all, delete-orphan")
    health_cards = relationship("HealthCard", back_populates="user", cascade="all, delete-orphan")

class FoodLog(Base):
    __tablename__ = "food_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False)
    image_file_id = Column(String, nullable=True)
    raw_text = Column(String, nullable=True)
    items_json = Column(JSON, nullable=False) # stores list of items
    calories = Column(Integer, nullable=False)
    proteins = Column(Float, nullable=False)
    fats = Column(Float, nullable=False)
    carbs = Column(Float, nullable=False)
    meal_type = Column(String(20), nullable=False, default="food")
    logged_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    user = relationship("User", back_populates="food_logs")

class WeightLog(Base):
    __tablename__ = "weight_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False)
    weight = Column(Float, nullable=False)
    logged_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    user = relationship("User", back_populates="weight_logs")

class MessageStat(Base):
    __tablename__ = "message_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False)
    message_type = Column(String(50), nullable=False) # 'text', 'photo', 'voice', etc.
    sent_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    user = relationship("User", back_populates="message_stats")

class AiRequestLog(Base):
    __tablename__ = "ai_request_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=True)
    request_type = Column(String(50), nullable=True)
    executed_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))

class AiRequestQueue(Base):
    __tablename__ = "ai_request_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    request_type = Column(String(50), nullable=False)  # 'analyze_food_input', 'adjust_food_analysis', 'adjust_meal_edit', 'generate_report'
    payload = Column(JSON, nullable=False)
    status = Column(String(20), nullable=False, default="pending")  # 'pending', 'processing', 'completed', 'failed', 'cancelled'
    error_message = Column(String, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime, nullable=True)
    last_error = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))
    processed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="ai_queues")

class Streak(Base):
    __tablename__ = "streaks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False)
    streak_type = Column(String(50), nullable=False) # e.g., 'food_logging', 'weight_logging', 'calorie_target_hit'
    current_count = Column(Integer, default=0, nullable=False)
    longest_count = Column(Integer, default=0, nullable=False)
    last_logged_date = Column(DateTime, nullable=True)
    started_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    user = relationship("User", back_populates="streaks")

class Achievement(Base):
    __tablename__ = "achievements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False)
    achievement_key = Column(String(100), nullable=False)
    unlocked_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    user = relationship("User", back_populates="achievements")

class HealthCard(Base):
    __tablename__ = "health_cards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False)
    week_start = Column(DateTime, nullable=False)
    card_data = Column(JSON, nullable=False) # stores overall_score, categories, achievements_this_week, coach_message
    generated_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    user = relationship("User", back_populates="health_cards")

