from typing import Tuple, Union, List
from PIL.Image import Image
from torch import Tensor
import pytorch_lightning as pl
from pytorch_lightning.utilities.types import EVAL_DATALOADERS
from pytorch_lightning.utilities.combined_loader import CombinedLoader
from torch.utils.data import DataLoader, Dataset, RandomSampler
from torchvision.datasets import CIFAR10, ImageFolder
from lightly.data import LightlyDataset
from lightly.transforms.utils import IMAGENET_NORMALIZE
from lightly.transforms import SimCLRTransform
import torchvision.transforms as T
import numpy as np 
import os
import torch
import re
from tqdm import tqdm

class CIFAR10_1(Dataset):
    def __init__(self, root, transform=None):
        self.transform = transform
        self.data = np.load(root)

    def __len__(self):
        return len(self.data)

    def __getitem__(self, index):
        sample = self.data[index]
        if self.transform is not None:
            sample = self.transform(sample)
        return sample, 10
    
class CIFARDataModule(pl.LightningDataModule):
    def __init__(self, args):
        super().__init__()
        self.cifar10_path = args.cifar10_path
        self.cifar10_1_path = args.cifar10_1_path
        self.mode = args.mode
        self.batch_size = args.batch_size
        assert self.mode in ['supervised', 'unsupervised', 'mmd']
        self.mmd_sample_sizes = args.mmd_sample_sizes
        self.mmd_n_tests = args.mmd_n_tests
        self.test_transform = T.ToTensor()

    def prepare_data(self):
        CIFAR10(root=self.cifar10_path, train=True, download=True)
        CIFAR10(root=self.cifar10_path, train=False, download=True)
        
    def setup(self, stage: str):
        if self.mode == 'supervised':
            if stage == 'fit':
                self.train_dataset = CIFAR10(root=self.cifar10_path, train=True, download=True,
                                             transform=T.Compose([T.RandomCrop(32, padding=4), 
                                                                 T.RandomHorizontalFlip(),
                                                                 T.ToTensor()])) 
                self.val_dataset = CIFAR10(root=self.cifar10_path, train=False, download=True, transform=self.test_transform)
            if stage == 'test' or stage == 'validate': 
                self.val_dataset = CIFAR10(root=self.cifar10_path, train=False, download=True, transform=self.test_transform)

        if self.mode == 'unsupervised':
            if stage == 'fit':
                train_dataset = CIFAR10(root=self.cifar10_path, train=True, download=True,
                                  transform=SimCLRTransform(input_size=32, gaussian_blur=0, normalize=None))
                val_dataset = CIFAR10(root=self.cifar10_path, train=False, download=True,
                                  transform=SimCLRTransform(input_size=32, gaussian_blur=0, normalize=None))
                self.train_dataset = LightlyDataset.from_torch_dataset(train_dataset)
                self.val_dataset = LightlyDataset.from_torch_dataset(val_dataset)

        if self.mode == 'mmd':
            if stage == 'test':
                self.dataset_s = CIFAR10(root=self.cifar10_path, train=False, download=True, transform=self.test_transform)
                self.dataset_q = CIFAR10_1(root=self.cifar10_1_path, transform=T.Compose([T.ToPILImage(), self.test_transform]))

    def train_dataloader(self): 
        return DataLoader(self.train_dataset, batch_size=self.batch_size, num_workers=8, 
                              pin_memory=True, drop_last=True, shuffle=True, persistent_workers=True) 
    
    def val_dataloader(self):
        return DataLoader(self.val_dataset, batch_size=self.batch_size, num_workers=8, 
                            pin_memory=True, drop_last=True, shuffle=False, persistent_workers=True) 
        
    def test_dataloader(self):
        if self.mode == 'supervised':
            return DataLoader(self.val_dataset, batch_size=self.batch_size, num_workers=8, 
                            pin_memory=True, drop_last=True, shuffle=False, persistent_workers=True) 
        if self.mode == 'mmd':
            batch_size = max(self.mmd_sample_sizes)
            num_samples = batch_size * self.mmd_n_tests
            sampler_s = RandomSampler(self.dataset_s, replacement=True, num_samples=3*num_samples)
            sampler_q = RandomSampler(self.dataset_q, replacement=True, num_samples=num_samples)
            dataloader_s = DataLoader(self.dataset_s, batch_size=batch_size*3, sampler=sampler_s,
                                    num_workers=8, pin_memory=True, drop_last=True, persistent_workers=True)
            dataloader_q = DataLoader(self.dataset_q, batch_size=batch_size, sampler=sampler_q,
                                    num_workers=8, pin_memory=True, drop_last=True, persistent_workers=True)
            
            return CombinedLoader({'s': dataloader_s, 'q': dataloader_q})

class CADetTransform():
    def __init__(
        self,
        num_tranforms: int = 50,
        input_size: int = 224,
        scale: float = 0.75,
        hf_prob: float = 0.5   
    ):
        view_transform = T.Compose([
            T.RandomResizedCrop(size=input_size, scale=(scale, scale)),
            T.RandomHorizontalFlip(p=hf_prob),
            T.ToTensor()
        ])
        self.transforms = [view_transform for _ in range(num_tranforms)]

    def __call__(self, image: Union[Tensor, Image]) -> Union[List[Tensor], List[Image]]:
         return [transform(image) for transform in self.transforms]

class ImageNetDataModule(pl.LightningDataModule):
    def __init__(self, args):
        super().__init__()
        self.train_path = args.train_path
        self.val_path = args.val_path
        self.imagenet_o_path = args.imagenet_o_path
        self.inaturalist_path = args.inaturalist_path
        self.pgd_path = args.pgd_path
        self.cw_path = args.cw_path
        self.fgsm_path = args.fgsm_path

        self.mode = args.mode
        assert self.mode in ['supervised', 'unsupervised', 'mmd', 'cadet']
        self.bacth_size = args.batch_size
        self.mmd_sample_sizes = args.mmd_sample_sizes
        self.mmd_n_tests = args.mmd_n_tests
        self.mmd_image_set_q = args.mmd_image_set_q
        assert self.mmd_image_set_q in ['imagenet_o', 'inaturalist', 'pgd', 'cw', 'fgsm']

        self.cadet_n_tests = args.cadet_n_tests
        self.cadet_n_transforms = args.cadet_n_transforms

        # self.normalize = T.Normalize(mean=IMAGENET_NORMALIZE["mean"], std=IMAGENET_NORMALIZE["std"])
        self.test_transform = T.Compose([T.Resize([256,256]),
                                        T.CenterCrop(size=224), 
                                        T.ToTensor()])
        
    def setup(self, stage: str):
        if self.mode == 'supervised':
            if stage == 'fit':
                self.train_dataset = ImageFolder(root=self.train_path, transform=T.Compose([T.RandomResizedCrop(224),
                                                                                            T.RandomHorizontalFlip(),
                                                                                            T.ToTensor()]))
                self.val_dataset = ImageFolder(root=self.val_path, transform=self.test_transform)

            if stage == 'test':
                # self.test_dataset = ImageFolder(root=self.val_path, transform=self.test_transform)   
                # self.test_dataset = ImageFolder(root=self.fgsm_path, transform=self.test_transform)  
                # self.test_dataset = ImageFolder(root=self.pgd_path, transform=self.test_transform)
                self.test_dataset = ImageFolder(root=self.cw_path, transform=self.test_transform) 
                # self.test_dataset = ImageFolder(root=self.imagenet_o_path, transform=self.test_transform) 

        if self.mode == 'unsupervised':
            if stage == 'fit':
                train_dataset = ImageFolder(root=self.train_path, transform=SimCLRTransform())                                
                val_dataset = ImageFolder(root=self.val_path, transform=SimCLRTransform())
                self.train_dataset = LightlyDataset.from_torch_dataset(train_dataset)
                self.val_dataset = LightlyDataset.from_torch_dataset(val_dataset)

        if self.mode == 'mmd':
            if stage == 'test':
                self.test_dataset_s = ImageFolder(root=self.val_path, transform=self.test_transform) 
                if self.mmd_image_set_q == 'imagenet_o':
                    self.test_dataset_q = ImageFolder(root=self.imagenet_o_path, transform=self.test_transform) 
                elif self.mmd_image_set_q == 'inaturalist':
                    self.test_dataset_q = ImageFolder(root=self.inaturalist_path, transform=self.test_transform) 
                elif self.mmd_image_set_q == 'pgd':
                    self.test_dataset_q = ImageFolder(root=self.pgd_path, transform=self.test_transform) 
                elif self.mmd_image_set_q == 'cw':
                    self.test_dataset_q = ImageFolder(root=self.cw_path, transform=self.test_transform) 
                elif self.mmd_image_set_q == 'fgsm':
                    self.test_dataset_q = ImageFolder(root=self.fgsm_path, transform=self.test_transform) 
               
        if self.mode == 'cadet':
            if stage == 'test':
                transform = CADetTransform(num_tranforms=self.cadet_n_transforms)
                self.test_dataset_same_dist = ImageFolder(root=self.train_path, transform=transform)
                # self.test_dataset_same_dist = ImageFolder(root=self.val_path, transform=transform)
                self.test_dataset_imagenet_o = ImageFolder(root=self.imagenet_o_path, transform=transform)
                self.test_dataset_inaturalist = ImageFolder(root=self.inaturalist_path, transform=transform)
                self.test_dataset_pgd = ImageFolder(root=self.pgd_path, transform=transform)
                self.test_dataset_cw = ImageFolder(root=self.cw_path, transform=transform)
                self.test_dataset_fgsm = ImageFolder(root=self.fgsm_path, transform=transform)
 
    def train_dataloader(self): 
        return DataLoader(self.train_dataset, batch_size=self.bacth_size, num_workers=8,
                          pin_memory=True, drop_last=True, shuffle=True, persistent_workers=True) 
    
    def val_dataloader(self):
        return DataLoader(self.val_dataset, batch_size=self.bacth_size, num_workers=8, 
                          pin_memory=True, drop_last=True, shuffle=False, persistent_workers=True) 
        
    def test_dataloader(self):
        if self.mode == 'supervised':
            return DataLoader(self.test_dataset, batch_size=self.bacth_size, num_workers=8, 
                              pin_memory=True, drop_last=True, shuffle=False, persistent_workers=True) 
        if self.mode == 'mmd':
            batch_size = max(self.mmd_sample_sizes)
            num_samples = batch_size * self.mmd_n_tests

            sampler_test_s = RandomSampler(self.test_dataset_s, replacement=True, num_samples=3*num_samples)
            sampler_test_q = RandomSampler(self.test_dataset_q, replacement=True, num_samples=3*num_samples)



            dataloader_test_s= DataLoader(self.test_dataset_s, batch_size=batch_size*3, sampler=sampler_test_s,
                                    num_workers=8, pin_memory=True, drop_last=True, persistent_workers=True)
            dataloader_test_q = DataLoader(self.test_dataset_q, batch_size=batch_size*3, sampler=sampler_test_q,
                                    num_workers=8, pin_memory=True, drop_last=True, persistent_workers=True) 
            return CombinedLoader({'s': dataloader_test_s, 'q': dataloader_test_q})
        
        if self.mode == 'cadet':
            sampler_test_same_dist = RandomSampler(self.test_dataset_same_dist, replacement=False, num_samples=self.cadet_n_tests)
            sampler_test_imagenet_o = RandomSampler(self.test_dataset_imagenet_o, replacement=False, num_samples=self.cadet_n_tests)
            sampler_test_inaturalist = RandomSampler(self.test_dataset_inaturalist, replacement=False, num_samples=self.cadet_n_tests)
            sampler_test_pgd = RandomSampler(self.test_dataset_pgd, replacement=False, num_samples=self.cadet_n_tests)
            sampler_test_cw = RandomSampler(self.test_dataset_cw, replacement=False, num_samples=self.cadet_n_tests)
            sampler_test_fgsm = RandomSampler(self.test_dataset_fgsm, replacement=False, num_samples=self.cadet_n_tests)
            dataloader_test_same_dist = DataLoader(self.test_dataset_same_dist, batch_size=1, sampler=sampler_test_same_dist, collate_fn=self.cadet_collate_fn)
            dataloader_test_imagenet_o = DataLoader(self.test_dataset_imagenet_o, batch_size=1, sampler=sampler_test_imagenet_o, collate_fn=self.cadet_collate_fn)
            dataloader_test_inaturalist = DataLoader(self.test_dataset_inaturalist, batch_size=1, sampler=sampler_test_inaturalist, collate_fn=self.cadet_collate_fn)
            dataloader_test_pgd = DataLoader(self.test_dataset_pgd, batch_size=1, sampler=sampler_test_pgd, collate_fn=self.cadet_collate_fn)
            dataloader_test_cw = DataLoader(self.test_dataset_cw, batch_size=1, sampler=sampler_test_cw, collate_fn=self.cadet_collate_fn)
            dataloader_test_fgsm = DataLoader(self.test_dataset_fgsm, batch_size=1, sampler=sampler_test_fgsm, collate_fn=self.cadet_collate_fn)
            return CombinedLoader({'imagenet': dataloader_test_same_dist, 'imagenet_o': dataloader_test_imagenet_o, 'inaturalist': dataloader_test_inaturalist, 
                                   'pgd': dataloader_test_pgd, 'cw': dataloader_test_cw, 'fgsm': dataloader_test_fgsm})

    @staticmethod
    def cadet_collate_fn(batch):
        X, _ = batch[0]
        return torch.stack(X)
    
class AttackDataset(ImageFolder):
    def __init__(self, image_path, transform):
        super().__init__(image_path, transform=transform)
        self.idx_to_class = {v: k for k, v in self.class_to_idx.items()}

    def __getitem__(self, index: int):
        path, target = self.samples[index]
        sample = self.loader(path)
        image_size = sample.size
        image_name = re.split("/|\\\\", path)[-1]
        if self.transform is not None:
            sample = self.transform(sample)

        return sample, target, torch.tensor(image_size), image_name

class DatasetAttacker:
    def __init__(self, image_path, target_path, attacker, device=torch.device('cpu'), input_size=[224,224]):
        self.image_path = image_path
        self.target_path = target_path
        self.attacker = attacker
        self.device = device
        self.pre_attack_transform = T.Compose([T.Resize(size=input_size, antialias=True), 
                                               T.ToTensor()])
        self.post_attack_transform = T.ToPILImage()
        self.dataset = AttackDataset(self.image_path, transform=self.pre_attack_transform)
                
    def attack(self, num_samples = None, batch_size=128):  
        sampler = None if num_samples is None else RandomSampler(self.dataset, num_samples=num_samples) 
        self.dataloader = DataLoader(self.dataset, batch_size=batch_size, sampler=sampler, shuffle=False)
        for _, (images, labels, image_sizes, image_names) in enumerate(tqdm(self.dataloader)):
            adv_images, labels = images.to(self.device), labels.to(self.device)
            adv_images = self.attacker(images, labels)
            self.save(adv_images, labels, image_sizes, image_names)
                               
    def save(self, images, labels, image_sizes, image_names):
        for image, label, image_size, image_name in zip(images, labels, image_sizes, image_names):
            x = self.post_attack_transform(image)
            # import matplotlib.pyplot as plt
            # plt.imshow(x)
            # plt.show()
            x = x.resize(image_size)
            class_name = self.dataset.idx_to_class[label.cpu().item()]
            target_path = os.path.join(self.target_path, class_name)
            os.makedirs(target_path, exist_ok=True)
            x.save(os.path.join(target_path, image_name), quality=100)

class AttackDataset(ImageFolder):
    def __init__(self, image_path, transform):
        super().__init__(image_path, transform=transform)
        self.idx_to_class = {v: k for k, v in self.class_to_idx.items()}

    def __getitem__(self, index: int):
        path, target = self.samples[index]
        sample = self.loader(path)
        image_size = sample.size
        image_name = re.split("/|\\\\", path)[-1]
        if self.transform is not None:
            sample = self.transform(sample)

        return sample, target, torch.tensor(image_size), image_name

class DatasetAttacker_NoResize:
    def __init__(self, image_path, target_path, attacker, device=torch.device('cpu')):
        self.image_path = image_path
        self.target_path = target_path
        self.attacker = attacker
        self.device = device
        self.pre_attack_transform = T.ToTensor()
        self.post_attack_transform = T.ToPILImage()
        self.dataset = AttackDataset(self.image_path, transform=self.pre_attack_transform)
                
    def attack(self, num_samples = None):  
        sampler = None if num_samples is None else RandomSampler(self.dataset, num_samples=num_samples) 
        self.dataloader = DataLoader(self.dataset, batch_size=1, sampler=sampler, shuffle=False)
        for _, (images, labels, image_sizes, image_names) in enumerate(tqdm(self.dataloader)):
            adv_images, labels = images.to(self.device), labels.to(self.device)
            adv_images = self.attacker(images, labels)
            self.save(adv_images, labels, image_sizes, image_names)
                               
    def save(self, images, labels, image_sizes, image_names):
        for image, label, _, image_name in zip(images, labels, image_sizes, image_names):
            x = self.post_attack_transform(image)
            # import matplotlib.pyplot as plt
            # plt.imshow(x)
            # plt.show()
            class_name = self.dataset.idx_to_class[label.cpu().item()]
            target_path = os.path.join(self.target_path, class_name)
            os.makedirs(target_path, exist_ok=True)
            x.save(os.path.join(target_path, image_name), quality=100)