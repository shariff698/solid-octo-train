"""ProSkills Online - single-file Django shop.
Commands:  python app.py setup | createsuperuser | runserver
Production: gunicorn app:application   (or a WSGI file that does: from app import application)"""
import sys
if __name__ == "__main__":  # always run through the importable module `app` (prevents double-loading)
    from app import cli
    cli()
    sys.exit()

import base64, json, os, re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import requests
import django
from django.apps import AppConfig
from django.conf import settings

BASE_DIR = Path(__file__).resolve().parent
DEBUG = os.getenv("DEBUG", "1") == "1"


class ShopConfig(AppConfig):
    name = "app"
    label = "shop"
    default_auto_field = "django.db.models.BigAutoField"


# ---------------------------------------------------------------- SETTINGS
if not settings.configured:
        for d in ("staticfiles", "media"):
        try:
            (BASE_DIR / d).mkdir(exist_ok=True)
        except OSError:
            pass
    settings.configure
        DEBUG=DEBUG,
        SECRET_KEY=os.getenv("SECRET_KEY", "change-me-in-production"),
        ALLOWED_HOSTS=["*"],
        CSRF_TRUSTED_ORIGINS=[o for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o],
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        ROOT_URLCONF="app",
        INSTALLED_APPS=[
            "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
            "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
            "app.ShopConfig",
        ],
        MIGRATION_MODULES={"shop": None},
        MIDDLEWARE=[
            "django.middleware.security.SecurityMiddleware",
            "whitenoise.middleware.WhiteNoiseMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
        ],
        TEMPLATES=[{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [BASE_DIR], "APP_DIRS": True,
            "OPTIONS": {"context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages"]},
        }],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}},
        STATIC_URL="/static/", STATIC_ROOT=BASE_DIR / "staticfiles",
        WHITENOISE_USE_FINDERS=True,
        MEDIA_URL="/media/", MEDIA_ROOT=BASE_DIR / "media",
        LOGIN_URL="/login/", LOGIN_REDIRECT_URL="/", LOGOUT_REDIRECT_URL="/",
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        # M-Pesa (Safaricom Daraja)
        MPESA_KEY=os.getenv("MPESA_KEY", ""),
        MPESA_SECRET=os.getenv("MPESA_SECRET", ""),
        MPESA_SHORTCODE=os.getenv("MPESA_SHORTCODE", "174379"),
        MPESA_PASSKEY=os.getenv("MPESA_PASSKEY", ""),
        MPESA_CALLBACK_URL=os.getenv("MPESA_CALLBACK_URL", "https://yourdomain.com/mpesa/callback/"),
        MPESA_BASE=os.getenv("MPESA_BASE", "https://sandbox.safaricom.co.ke"),
        # Demo auto-confirm is ONLY allowed while DEBUG=1 and no M-Pesa keys (never in production)
        DEMO_MODE=DEBUG and not os.getenv("MPESA_KEY"),
    )
django.setup()

from django import forms
from django.contrib import admin, messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView, LogoutView
from django.core.management import call_command, execute_from_command_line
from django.core.wsgi import get_wsgi_application
from django.db import connection, models
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt


# ---------------------------------------------------------------- MODELS (one database)
class Product(models.Model):
    title = models.CharField(max_length=160)
    slug = models.SlugField(unique=True, blank=True)
    category = models.CharField(max_length=60, default="eBooks")
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    image_url = models.URLField(blank=True)
    file = models.FileField(upload_to="products/")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "shop"
        db_table = "shop_product"
        ordering = ["-created"]

    def save(self, *a, **k):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*a, **k)

    def __str__(self):
        return self.title


class Order(models.Model):
    STATUS = [("pending", "Pending"), ("paid", "Paid"), ("failed", "Failed")]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="orders")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    phone = models.CharField(max_length=15)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    checkout_id = models.CharField(max_length=100, blank=True)
    receipt = models.CharField(max_length=40, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "shop"
        db_table = "shop_order"
        ordering = ["-created"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "price")
    prepopulated_fields = {"slug": ("title",)}
    search_fields = ("title",)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "product", "amount", "status", "receipt", "created")
    list_filter = ("status",)


def setup_db():
    """Creates Django's tables plus Product/Order. Safe to run many times."""
    call_command("migrate", verbosity=1)
    existing = connection.introspection.table_names()
    with connection.schema_editor() as editor:
        for m in (Product, Order):
            if m._meta.db_table not in existing:
                editor.create_model(m)
    print("Database ready. Next: python app.py createsuperuser")


# ---------------------------------------------------------------- M-PESA
def mpesa_token():
    r = requests.get(f"{settings.MPESA_BASE}/oauth/v1/generate?grant_type=client_credentials",
                     auth=(settings.MPESA_KEY, settings.MPESA_SECRET), timeout=20)
    return r.json()["access_token"]


def stk_push(phone, amount, ref):
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    pwd = base64.b64encode(f"{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{ts}".encode()).decode()
    body = {
        "BusinessShortCode": settings.MPESA_SHORTCODE, "Password": pwd, "Timestamp": ts,
        "TransactionType": "CustomerPayBillOnline", "Amount": int(amount), "PartyA": phone,
        "PartyB": settings.MPESA_SHORTCODE, "PhoneNumber": phone,
        "CallBackURL": settings.MPESA_CALLBACK_URL, "AccountReference": ref,
        "TransactionDesc": "Digital product",
    }
    r = requests.post(f"{settings.MPESA_BASE}/mpesa/stkpush/v1/processrequest", json=body,
                      headers={"Authorization": f"Bearer {mpesa_token()}"}, timeout=20)
    return r.json()


# ---------------------------------------------------------------- VIEWS
def index(request):
    q, cat = request.GET.get("q", "").strip(), request.GET.get("cat", "")
    items = Product.objects.all()
    if q:
        items = items.filter(Q(title__icontains=q) | Q(description__icontains=q) | Q(category__icontains=q))
    if cat:
        items = items.filter(category=cat)
    cats = Product.objects.values_list("category", flat=True).distinct()
    return render(request, "index.html", {"page": "home", "products": items, "q": q, "cat": cat, "cats": cats})


def search_api(request):
    q = request.GET.get("q", "").strip()
    rows = Product.objects.filter(title__icontains=q)[:6] if q else []
    return JsonResponse({"results": [{"title": p.title, "price": str(p.price), "url": f"/p/{p.slug}/"} for p in rows]})


class SignupForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")


def signup(request):
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)
        user.email = form.cleaned_data["email"]
        user.save()
        login(request, user)
        messages.success(request, "Welcome! Your account is ready.")
        return redirect("index")
    return render(request, "index.html", {"page": "signup", "form": form})


def detail(request, slug):
    p = get_object_or_404(Product, slug=slug)
    owned = request.user.is_authenticated and Order.objects.filter(user=request.user, product=p, status="paid").first()
    return render(request, "index.html", {"page": "detail", "p": p, "owned": owned})


def normalize_phone(raw):
    n = re.sub(r"\D", "", raw)
    if n.startswith("0"):
        n = "254" + n[1:]
    elif n.startswith("7") or n.startswith("1"):
        n = "254" + n
    return n if re.fullmatch(r"254[17]\d{8}", n) else None


@login_required
def checkout(request, slug):
    p = get_object_or_404(Product, slug=slug)
    if request.method == "POST":
        phone = normalize_phone(request.POST.get("phone", ""))
        if not phone:
            messages.error(request, "Enter a valid M-Pesa number, e.g. 0712345678.")
            return render(request, "index.html", {"page": "checkout", "p": p})
        o = Order.objects.create(user=request.user, product=p, phone=phone, amount=p.price)
        if settings.MPESA_KEY:
            try:
                res = stk_push(phone, p.price, f"ORD{o.pk}")
                o.checkout_id = res.get("CheckoutRequestID", "")
                if not o.checkout_id:
                    o.status = "failed"
            except Exception:
                o.status = "failed"
            o.save()
        elif settings.DEMO_MODE:
            o.status, o.receipt = "paid", f"DEMO{o.pk:06d}"
            o.save()
        else:
            o.status = "failed"
            o.save()
            messages.error(request, "Payments are not set up yet. Please contact the shop owner.")
        return redirect("order", pk=o.pk)
    return render(request, "index.html", {"page": "checkout", "p": p})


@login_required
def order_page(request, pk):
    o = get_object_or_404(Order, pk=pk, user=request.user)
    return render(request, "index.html", {"page": "order", "o": o, "demo": settings.DEMO_MODE})


@login_required
def order_status(request, pk):
    return JsonResponse({"status": get_object_or_404(Order, pk=pk, user=request.user).status})


@csrf_exempt
def mpesa_callback(request):
    try:
        cb = json.loads(request.body)["Body"]["stkCallback"]
        o = Order.objects.get(checkout_id=cb["CheckoutRequestID"], status="pending")
        if cb["ResultCode"] == 0:
            items = {i["Name"]: i.get("Value") for i in cb["CallbackMetadata"]["Item"]}
            if Decimal(str(items.get("Amount", 0))) >= o.amount:  # only accept the full amount
                o.status, o.receipt = "paid", str(items.get("MpesaReceiptNumber", ""))
            else:
                o.status = "failed"
        else:
            o.status = "failed"
        o.save()
    except Exception:
        pass
    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


@login_required
def library(request):
    return render(request, "index.html", {"page": "library", "orders": request.user.orders.select_related("product")})


@login_required
def download(request, pk):
    o = get_object_or_404(Order, pk=pk, user=request.user, status="paid")
    if not o.product.file:
        raise Http404
    return FileResponse(o.product.file.open("rb"), as_attachment=True, filename=o.product.file.name.split("/")[-1])


def asset(request, name):
    types = {"style.css": "text/css", "app.js": "application/javascript"}
    return FileResponse(open(BASE_DIR / name, "rb"), content_type=types[name])


# ---------------------------------------------------------------- URLS
urlpatterns = [
    path("admin/", admin.site.urls),
    path("", index, name="index"),
    path("style.css", asset, {"name": "style.css"}),
    path("app.js", asset, {"name": "app.js"}),
    path("api/search/", search_api, name="search_api"),
    path("signup/", signup, name="signup"),
    path("login/", LoginView.as_view(template_name="index.html", extra_context={"page": "login"}), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("p/<slug:slug>/", detail, name="detail"),
    path("p/<slug:slug>/buy/", checkout, name="checkout"),
    path("order/<int:pk>/", order_page, name="order"),
    path("order/<int:pk>/status/", order_status, name="order_status"),
    path("mpesa/callback/", mpesa_callback),
    path("library/", library, name="library"),
    path("download/<int:pk>/", download, name="download"),
]

application = get_wsgi_application()  # gunicorn app:application


def cli():
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup_db()
    else:
        execute_from_command_line(sys.argv)
